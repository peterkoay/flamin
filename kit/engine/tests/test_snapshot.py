"""Targeted Git-object verification and trusted lock regression cases."""

import json

from helpers import Project

from flaminlib.snapshot import verify_snapshot
from flaminlib.util import sha256_text


class SnapshotTests(Project):
    copy_kit = False
    path = "extension.py"
    initial = "# FLAMIN:LOCK STRUCTURE_LOCKED\ndef run():\n    # FLAMIN:EXTENSION body\n    pass\n    # FLAMIN:EXTENSION:END\n"

    def git_ok(self, *args):
        result = self.git(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def setUp(self):
        super().setUp()
        self.git_ok("init", "-q")
        self.write(self.path, self.initial)
        self.git_ok("add", ".")
        self.commit("base")
        self.base = self.git_ok("rev-parse", "HEAD")

    def commit(self, message):
        self.git_ok("-c", "user.name=Snapshot test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", message)

    def test_staged_and_committed_extension_ignore_worktree(self):
        self.write(self.path, self.initial.replace("    pass", "    return 1"))
        self.git_ok("add", self.path)
        report = verify_snapshot(self.root, self.base)
        self.assertEqual(report["problems"], [])
        self.write(self.path, "Unrelated working-tree contents\n")
        self.assertEqual(verify_snapshot(self.root, self.base), report)
        self.commit("valid extension")
        self.assertEqual(verify_snapshot(self.root, self.base, self.git_ok("rev-parse", "HEAD")), report)

    def test_lock_record_and_sidecar_cannot_authorize_structure_change(self):
        self.write(self.path, self.initial.replace("run()", "changed()"))
        path = ".flamin/locks.json"
        text = json.dumps({"files": {self.path: {"level": "STRUCTURE_LOCKED"}}})
        self.write(path, text)
        self.write(path + ".sha256", sha256_text(text) + "\n")
        self.git_ok("add", ".")
        problems = verify_snapshot(self.root, self.base)["problems"]
        self.assertTrue(any("protected engine" in item for item in problems))
        self.assertTrue(any("outside every extension" in item for item in problems))
        self.assertFalse(any("checksum mismatch" in item for item in problems))
