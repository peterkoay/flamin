"""State safety, audit logging, secret redaction."""
import json
import multiprocessing
import os
import subprocess
import sys
import threading
import time
import unittest

from helpers import ENGINE, Project

from flaminlib import audit
from flaminlib.secrets_scan import find_secrets, redact, redact_obj
from flaminlib.statefile import StateLock, Store, atomic_write_bytes, pid_alive
from flaminlib.util import FlaminError, glob_match


class TestStateSafety(Project):
    copy_kit = False

    def setUp(self):
        super().setUp()
        (self.root / ".flamin").mkdir()
        self.store = Store(self.root)

    def test_atomic_write_and_checksum(self):
        self.store.save("state", {"a": 1})
        self.assertEqual(self.store.load("state"), {"a": 1})
        self.assertEqual(self.store.checksum_problems(), [])
        p = self.root / ".flamin" / "state.json"
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))  # git checkout with CRLF is not tampering
        self.assertEqual(self.store.checksum_problems(), [])
        p.write_text('{"a": 2}\n', encoding="utf-8")
        self.assertTrue(any("checksum mismatch" in p for p in self.store.checksum_problems()))

    def test_second_writer_waits_then_fails_cleanly(self):
        self.store.save("state", {"n": 0})
        holder = subprocess.Popen([sys.executable, "-B", "-c", (
            "import sys,time;sys.path.insert(0,r'%s');from flaminlib.statefile import StateLock;"
            "from pathlib import Path;l=StateLock(Path(r'%s'));l.acquire();print('held',flush=True);time.sleep(3);l.release()"
        ) % (ENGINE, self.root)], stdout=subprocess.PIPE, text=True)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        t0 = time.monotonic()
        with self.assertRaises(FlaminError) as ctx:
            with self.store.transaction():
                self.store.save("state", {"n": 99})
        waited = time.monotonic() - t0
        self.assertGreaterEqual(waited, 0.9)
        self.assertIn("Another flamin command is writing state", str(ctx.exception))
        holder.wait()
        self.assertEqual(self.store.load("state"), {"n": 0})  # nothing overwritten, nothing lost

    def test_two_writers_no_lost_update(self):
        self.store.save("state", {"n": 0})
        os.environ["FLAMIN_LOCK_WAIT"] = "20"
        code = ("import sys;sys.path.insert(0,r'%s');from flaminlib.statefile import Store;from pathlib import Path;"
                "s=Store(Path(r'%s'))\nfor i in range(25):\n    with s.transaction():\n        d=s.load('state');d['n']+=1;s.save('state',d)\n") % (ENGINE, self.root)
        env = dict(os.environ, FLAMIN_LOCK_WAIT="20")
        procs = [subprocess.Popen([sys.executable, "-B", "-c", code], env=env) for _ in range(2)]
        for p in procs:
            self.assertEqual(p.wait(), 0)
        self.assertEqual(self.store.load("state")["n"], 50)

    def test_lock_never_stolen_and_pid_check(self):
        lock_path = self.root / ".flamin" / ".lock"
        lock_path.write_text(json.dumps({"pid": 999999, "session": "s-dead", "started": "x"}), encoding="utf-8")
        with self.assertRaises(FlaminError):
            with StateLock(self.root, wait=0.2):
                pass
        self.assertTrue(lock_path.exists())
        self.assertTrue(pid_alive(os.getpid()))
        self.assertFalse(pid_alive(999999))

    def test_reentrant(self):
        with StateLock(self.root):
            with StateLock(self.root):
                self.store.save("state", {"x": 1})
        self.assertFalse((self.root / ".flamin" / ".lock").exists())


class TestAudit(Project):
    copy_kit = False

    def setUp(self):
        super().setUp()
        (self.root / ".flamin").mkdir()

    def test_redaction_and_personal_fields(self):
        e = audit.make_entry("write", target="x.py", reason="key sk-proj-AbCdEfGhIjKlMnOpQrStUvWx0123", root=self.root,
                             payload={"user_email": "a@b.c", "content": "token = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789AB'"})
        audit.append(e, root=self.root)
        text = "".join(p.read_text(encoding="utf-8") for p in (self.root / ".flamin" / "audit").glob("*.jsonl"))
        self.assertNotIn("sk-proj-AbCd", text)
        self.assertNotIn("ghp_abcdef", text)
        self.assertNotIn("a@b.c", text)
        self.assertIn("[REDACTED:openai-key]", text)
        self.assertIn("[REDACTED:github-token]", text)

    def test_spill_when_locked_then_merged(self):
        with StateLock(self.root):
            # another process holds the lock: simulate by waiting 0 in a thread-free way
            pass
        lock = self.root / ".flamin" / ".lock"
        lock.write_text(json.dumps({"pid": os.getpid() + 1, "session": "s-other", "started": "x"}), encoding="utf-8")
        for i in range(3):
            where = audit.append(audit.make_entry("step", target=f"t{i}", root=self.root), root=self.root, wait=0.1)
            self.assertEqual(where, "spill")
        spills = list((self.root / ".flamin" / "audit").glob("spill-*.jsonl"))
        self.assertEqual(len(spills), 1)
        lock.unlink()
        self.assertEqual(audit.append(audit.make_entry("step", target="t3", root=self.root), root=self.root), "log")
        self.assertEqual(list((self.root / ".flamin" / "audit").glob("spill-*.jsonl")), [])
        targets = [e["target"] for e in audit.read_all(self.root)]
        self.assertEqual(sorted(targets), ["t0", "t1", "t2", "t3"])  # none lost

    def test_retention_age_and_size(self):
        d = self.root / ".flamin" / "audit"
        d.mkdir(parents=True)
        (d / "2020-01-01.jsonl").write_text("{}\n")
        (d / "2099-01-01.jsonl").write_text("x" * 50 + "\n")
        (d / "2099-01-02.jsonl").write_text("x" * 50 + "\n")
        deleted = audit.retention(self.root, max_bytes=60)
        self.assertTrue(any("2020-01-01" in x for x in deleted))
        self.assertTrue(any("2099-01-01" in x for x in deleted))
        self.assertTrue((d / "2099-01-02.jsonl").exists())
        logged = [e for e in audit.read_all(self.root) if e.get("action") == "retention-delete"]
        self.assertEqual(len(logged), 2)

    def test_readable_and_stats(self):
        e = {"ts": "2026-10-03T08:15:22Z", "tool": "codex", "agent": "developer", "decision": "deny",
             "action": "write", "target": "modules/booking/x.py", "reason": "locked lines 12-14"}
        self.assertEqual(audit.readable(e), "08:15 codex developer DENY write modules/booking/x.py (locked lines 12-14)")
        st = audit.stats([dict(e, session="s1")] * 3 + [dict(e, session="s2")])
        self.assertEqual(st["developer"], {"tasks": 2, "avg_steps": 2.0, "max_steps": 3})


class TestSecrets(unittest.TestCase):
    def test_patterns(self):
        for text, kind in [("AKIAABCDEFGHIJKLMNOP", "aws-access-key"),
                           ("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----", "private-key"),
                           ("password = 'hunter2hunter2'", "password"),
                           ("api_key: 'Zx9Qw3Er7Ty1Ui5Op2As8Df4'", "high-entropy-secret")]:
            spans = find_secrets(text)
            self.assertTrue(spans and spans[0][0] == kind, (text, spans))

    def test_no_false_positive_on_placeholders(self):
        self.assertEqual(find_secrets("password = '${DB_PASSWORD}'"), [])
        self.assertEqual(find_secrets("token = 'your_token_here'"), [])
        self.assertEqual(find_secrets("def check_password(pw):"), [])

    def test_redact_obj(self):
        o = redact_obj({"user_email": "x@y.z", "cmd": "curl -H 'Authorization: sk-ant-abcdefghijklmnopqrstuvwx'"})
        self.assertNotIn("user_email", o)
        self.assertIn("[REDACTED:anthropic-key]", o["cmd"])
        self.assertEqual(redact("plain text"), "plain text")


class TestGlob(unittest.TestCase):
    def test_glob(self):
        self.assertTrue(glob_match(".flamin/state.json", ".flamin/*.json"))
        self.assertFalse(glob_match(".flamin/design/a.json", ".flamin/*.json"))
        self.assertTrue(glob_match("kit/engine/x.py", "kit/**"))
        self.assertTrue(glob_match("tests/a/test_x.py", "tests/**"))
        self.assertTrue(glob_match("a/b/c.pem", "**/*.pem"))


if __name__ == "__main__":
    unittest.main()
