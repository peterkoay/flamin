"""Test helpers: throwaway product projects in a temp folder, outside the kit."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
KIT = ENGINE.parent
REPO = KIT.parent
sys.path.insert(0, str(ENGINE))
sys.dont_write_bytecode = True

from flaminlib import cli, hooks  # noqa: E402

MODEL = """module = "member"
[[entities]]
name = "Member"
aggregate_root = true
id = "id"
fields = [ { name = "id", type = "string" }, { name = "phone", type = "string" },
           { name = "password_hash", type = "string", secret = true } ]
"""
WRITE_SPEC = """kind = "write"
entity = "Member"
[api]
method = "POST"
path = "/api/member/register"
input = [ { name = "phone", type = "string", required = true }, { name = "password", type = "string", required = true } ]
output = [ { name = "id" }, { name = "phone" } ]
[write_plan]
aggregate_root = "Member"
transient = ["password"]
[[extension_points]]
name = "validateAggregate"
"""
QUERY_SPEC = """kind = "query"
entity = "Member"
[api]
method = "GET"
path = "/api/member/by-phone"
input = [ { name = "phone", type = "string", required = true } ]
output = [ { name = "id" }, { name = "phone" } ]
[query]
filter = ["phone"]
[[extension_points]]
name = "postProcessData"
"""
INTAKE = ["name=demo", "purpose=test", "users=members", "journeys=register", "features=register",
          "platforms=backend", "data_sensitivity=phone numbers", "success=it works"]


class Project(unittest.TestCase):
    """Each test gets a fresh product folder with a copy of the kit."""

    copy_kit = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="flamin-test-"))
        self.root = self.tmp / "product"
        self.root.mkdir()
        if self.copy_kit:
            shutil.copytree(KIT, self.root / "kit", ignore=shutil.ignore_patterns("__pycache__", "tests"))
            for n in ("flamin", "flamin.cmd"):
                if (REPO / n).exists():
                    shutil.copy2(REPO / n, self.root / n)
        self._env = dict(os.environ)
        os.environ["FLAMIN_ROOT"] = str(self.root)
        os.environ["FLAMIN_SESSION"] = "s-test"
        os.environ["FLAMIN_LOCK_WAIT"] = "1"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

        def _writable_then_retry(func, path, _exc):  # git makes object files read-only on Windows
            os.chmod(path, stat.S_IWRITE)
            func(path)
        shutil.rmtree(self.tmp, onexc=_writable_then_retry) if sys.version_info >= (3, 12) else \
            shutil.rmtree(self.tmp, onerror=_writable_then_retry)

    # ------------------------------------------------------------ running
    def flamin(self, *args, stdin: str | None = None, expect: int | None = 0) -> str:
        buf_out, buf_err = io.StringIO(), io.StringIO()
        old_in = sys.stdin
        sys.stdin = io.StringIO(stdin or "")  # never a terminal in tests
        try:
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                code = cli.main(list(args))
        finally:
            sys.stdin = old_in
        text = buf_out.getvalue() + buf_err.getvalue()
        if expect is not None:
            self.assertEqual(code, expect, f"flamin {' '.join(args)} exit {code}:\n{text}")
        self.last_code = code
        return text

    def hook(self, tool: str, payload: dict, event: str = "pretool") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = hooks.run(event, tool, json.dumps(payload))
        return code, out.getvalue(), err.getvalue()

    def git(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True)

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return p

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def state(self) -> dict:
        return json.loads(self.read(".flamin/state.json"))

    def approvals(self) -> dict:
        return json.loads(self.read(".flamin/approvals.json"))

    def last_request(self) -> str:
        return sorted(self.approvals()["requests"])[-1]

    def abs(self, rel: str) -> str:
        return str(self.root / rel)

    # ------------------------------------------------------------ flow shortcuts
    def init(self, tool: str = "claude"):
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "flamin test")
        self.flamin("init", "--tool", tool)

    def to_intake_done(self):
        self.init()
        args = []
        for kv in INTAKE:
            args += ["--set", kv]
        self.flamin("intake", *args)

    def to_stack(self, profile: str = "python-fastapi"):
        self.to_intake_done()
        self.write(".flamin/decisions/stack.md", f"Use {profile}: simple and known to the team.\n")
        self.flamin("intake", "--stack", profile)
        self.flamin("approve", self.last_request(), "--yes")

    def to_baseline(self, profile: str = "python-fastapi", model: str = MODEL):
        self.to_stack(profile)
        self.write(".flamin/analysis/member/requirements.md", "R1 register. R2 query by phone.\n")
        self.flamin("analyze", "member")
        self.flamin("model", "--add-module", "member")
        self.flamin("model", "--step", "2")
        self.write(".flamin/design/member/model.toml", model)
        self.flamin("model", "--step", "3")
        self.flamin("model", "--step", "4")
        self.flamin("baseline-review")
        self.flamin("approve", self.last_request(), "--yes")

    def to_generated(self, profile: str = "python-fastapi"):
        self.to_baseline(profile)
        self.write(".flamin/analysis/member/plan.md", "## Batch 1\n- POST /api/member/register\n- GET /api/member/by-phone\n")
        self.flamin("plan", "member")
        self.flamin("approve", self.last_request(), "--yes")
        self.write(".flamin/design/member/register.toml", WRITE_SPEC)
        self.write(".flamin/design/member/query-by-phone.toml", QUERY_SPEC)
        for s in ("register", "query-by-phone"):
            self.flamin("design", "member", f".flamin/design/member/{s}.toml")
            self.flamin("generate", f".flamin/design/member/{s}.toml")

    def claude_edit(self, rel: str, old: str, new: str, agent: str | None = "developer", mode: str = "default") -> dict:
        p = {"hook_event_name": "PreToolUse", "session_id": "sess-1", "permission_mode": mode,
             "tool_name": "Edit", "tool_input": {"file_path": self.abs(rel), "old_string": old, "new_string": new}}
        if agent:
            p.update({"agent_id": "a-1", "agent_type": agent})
        return p

    def claude_write(self, rel: str, content: str, agent: str | None = "developer", mode: str = "default") -> dict:
        p = {"hook_event_name": "PreToolUse", "session_id": "sess-1", "permission_mode": mode,
             "tool_name": "Write", "tool_input": {"file_path": self.abs(rel), "content": content}}
        if agent:
            p.update({"agent_id": "a-1", "agent_type": agent})
        return p

    def claude_bash(self, command: str, mode: str = "default", agent: str | None = None) -> dict:
        p = {"hook_event_name": "PreToolUse", "session_id": "sess-1", "permission_mode": mode,
             "tool_name": "Bash", "tool_input": {"command": command}}
        if agent:
            p.update({"agent_id": "a-1", "agent_type": agent})
        return p

    def audit_lines(self) -> list[dict]:
        out = []
        d = self.root / ".flamin" / "audit"
        for f in sorted(d.glob("*.jsonl")):
            out += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        return out
