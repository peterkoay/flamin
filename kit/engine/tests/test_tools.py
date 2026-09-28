"""Product tool selection: init renders, doctor checks and status warns only for the product's AI tool(s)."""
import json
import os
import shutil
import subprocess
from unittest import mock

from helpers import Project

from flaminlib import cmds
from flaminlib.statefile import Store

CLAUDE_MARKERS = ("CLAUDECODE", "CLAUDE_PROJECT_DIR")


class ToolProject(Project):
    def setUp(self):
        super().setUp()
        for k in CLAUDE_MARKERS:  # the test runner may itself run inside Claude Code
            os.environ.pop(k, None)
        self.git("init", "-q")

    def rendered(self):
        return {name: (self.root / name).exists() for name in
                (".claude/settings.json", ".claude/settings.local.json", "CLAUDE.md", ".codex/config.toml",
                 ".cursor/hooks.json", ".cursor/agents", "AGENTS.md")}


class TestInitTools(ToolProject):
    def test_claude_detected(self):
        os.environ["CLAUDECODE"] = "1"
        out = self.flamin("init")
        self.assertIn("Adapters for claude (detected: Claude Code)", out)
        self.assertNotIn("ACTION", out)
        r = self.rendered()
        self.assertTrue(r[".claude/settings.json"] and r[".claude/settings.local.json"] and r["CLAUDE.md"])
        self.assertFalse(r[".codex/config.toml"] or r[".cursor/hooks.json"] or r[".cursor/agents"] or r["AGENTS.md"])
        self.assertEqual(self.state()["tools"], ["claude"])

    def test_no_marker_defaults_to_claude(self):
        out = self.flamin("init")
        self.assertIn("Adapters for claude (default: no AI tool detected)", out)
        self.assertEqual(self.state()["tools"], ["claude"])
        self.assertFalse(self.rendered()[".codex/config.toml"])

    def test_all_is_explicit(self):
        out = self.flamin("init", "--tool", "all")
        self.assertTrue(all(self.rendered().values()))
        self.assertEqual(self.state()["tools"], ["claude", "codex", "cursor"])
        self.assertIn("ACTION: Codex", out)

    def test_adding_a_tool_keeps_the_first(self):
        self.flamin("init")
        self.flamin("init", "--tool", "codex")
        self.assertEqual(self.state()["tools"], ["claude", "codex"])
        out = self.flamin("init")  # no --tool on an existing product: keep its tools, remove nothing
        self.assertEqual(self.state()["tools"], ["claude", "codex"])
        self.assertIn("product state", out)


class TestDoctorTools(ToolProject):
    def _no_codex(self):
        real_which, real_run = shutil.which, subprocess.run

        def which(name, *a, **k):
            if "codex" in str(name).lower():
                raise AssertionError("doctor looked for codex on a claude-only product")
            return real_which(name, *a, **k)

        def run(args, *a, **k):
            if any("codex" in str(x).lower() for x in (args if isinstance(args, (list, tuple)) else [args])):
                raise AssertionError("doctor ran codex on a claude-only product")
            return real_run(args, *a, **k)
        return mock.patch("shutil.which", which), mock.patch("subprocess.run", run)

    def test_claude_only_product(self):
        os.environ["CLAUDECODE"] = "1"
        self.flamin("init")
        w, r = self._no_codex()
        with w, r:
            out = self.flamin("doctor", expect=None)
        self.assertIn("Codex: not used by this product; checks skipped.", out)
        self.assertIn("Claude Code: the PreToolUse hook runs as Claude Code runs it and denies a destructive command", out)
        self.assertIn("Claude Code: .claude/settings.json is current.", out)
        self.assertNotIn("FAIL Claude Code", out)

    def test_status_names_only_product_tools(self):
        os.environ["CLAUDECODE"] = "1"
        self.flamin("init")
        (self.root / ".codex").mkdir()
        (self.root / ".codex" / "config.toml").write_text("[agents]\n", encoding="utf-8")  # leftover from a kit copy
        out = self.flamin("status")
        self.assertIn("hook files present for: claude", out)
        self.assertNotIn("codex", out.split("hook files present for:")[1].split(";")[0])

    def test_legacy_product_behaves_as_before(self):
        self.flamin("init", "--tool", "all")
        st = self.state()
        st.pop("tools")
        Store(self.root).save("state", st)  # a product made before state['tools'] existed
        calls = []
        with mock.patch.object(cmds, "codex_checks", lambda *a, **k: calls.append(a)):
            out = self.flamin("doctor", expect=None)
        self.assertEqual(len(calls), 1)
        self.assertIn("adapter files present", out)
        self.assertIn("Claude Code:", out)

    def test_broken_claude_hook_is_reported(self):
        os.environ["CLAUDECODE"] = "1"
        self.flamin("init")
        s = json.loads(self.read(".claude/settings.local.json"))
        s["hooks"]["PreToolUse"][0]["hooks"][0]["args"][0] = "${CLAUDE_PROJECT_DIR}/kit/engine/missing.py"
        self.write(".claude/settings.local.json", json.dumps(s))
        out = self.flamin("doctor", expect=1)
        self.assertIn("FAIL Claude Code: .claude/settings.local.json differs", out)
        self.assertIn("FAIL Claude Code: the PreToolUse hook did not deny", out)

    def test_prune_is_gated(self):
        self.flamin("init", "--tool", "all")
        out = self.flamin("doctor", "--fix", "--tool", "claude", "--prune", expect=None)
        self.assertIn("Approval needed", out)
        self.assertIn(".codex/config.toml", out)  # the file list is shown
        self.assertTrue((self.root / ".codex" / "config.toml").exists())  # nothing goes without an answer
        self.assertTrue((self.root / "AGENTS.md").exists())
        rid = [r for r in self.approvals()["requests"].values() if r["kind"] == "prune"][0]["id"]
        self.flamin("approve", rid, "--yes")
        out = self.flamin("doctor", "--fix", "--tool", "claude", "--prune", expect=None)
        self.assertIn("Pruned", out)
        self.assertFalse((self.root / ".codex").exists() or (self.root / ".cursor").exists())
        self.assertFalse((self.root / "AGENTS.md").exists())
        self.assertTrue((self.root / ".claude" / "settings.json").exists() and (self.root / "CLAUDE.md").exists())
        self.assertEqual(self.state()["tools"], ["claude"])
        self.assertIn("Codex: not used by this product; checks skipped.", out)

    def test_prune_rejected_keeps_files(self):
        self.flamin("init", "--tool", "all")
        self.flamin("doctor", "--fix", "--tool", "claude", "--prune", expect=None)
        rid = [r for r in self.approvals()["requests"].values() if r["kind"] == "prune"][0]["id"]
        self.flamin("approve", rid, "--no")
        self.flamin("doctor", "--fix", "--tool", "claude", "--prune", expect=None)
        self.assertTrue((self.root / ".codex" / "config.toml").exists())
        self.assertEqual(self.state()["tools"], ["claude", "codex", "cursor"])


class TestRenderLineEndings(ToolProject):
    def test_crlf_checkout_is_current(self):
        self.flamin("init", "--tool", "all")
        for rel in (".codex/config.toml", "CLAUDE.md"):
            f = self.root / rel
            f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
        out = self.flamin("init")
        self.assertIn("already current", out)
        self.assertNotIn("ACTION", out)


class TestCursorChecks(ToolProject):
    def test_cursor_product(self):
        self.flamin("init", "--tool", "cursor")
        out = self.flamin("doctor", expect=None)
        self.assertIn("Cursor: the preToolUse hook runs as Cursor runs it and denies a destructive command", out)
        self.assertIn("adapter file(s) current", out)
        self.assertIn("Cursor: no hook has run yet in this product", out)
        self.assertNotIn("FAIL Cursor", out)
        self.assertIn("Codex: not used by this product; checks skipped.", out)

    def test_claude_only_skips_cursor(self):
        os.environ["CLAUDECODE"] = "1"
        self.flamin("init")
        out = self.flamin("doctor", expect=None)
        self.assertIn("Cursor: not used by this product; checks skipped.", out)

    def test_broken_cursor_hook_is_reported(self):
        self.flamin("init", "--tool", "cursor")
        h = json.loads(self.read(".cursor/hooks.json"))
        h["hooks"]["preToolUse"][0]["command"] = "exit 0"
        self.write(".cursor/hooks.json", json.dumps(h))
        out = self.flamin("doctor", expect=1)
        self.assertIn("FAIL Cursor: .cursor/hooks.json differs", out)
        self.assertIn("FAIL Cursor: the preToolUse hook did not deny", out)
