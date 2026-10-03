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

    def test_all_is_refused_before_product_creation(self):
        self.flamin("init", "--tool", "all", expect=1)
        self.assertFalse((self.root / ".flamin/state.json").exists())

    def test_switch_requires_human_answer(self):
        self.flamin("init")
        out = self.flamin("init", "--tool", "codex")
        self.assertIn("Approval needed", out)
        self.assertEqual(self.state()["tools"], ["claude"])
        self.assertFalse(self.rendered()[".codex/config.toml"])
        rid = self.last_request()
        self.flamin("approve", rid, "--yes")
        out = self.flamin("init", "--tool", "codex")
        self.assertIn("Adapters for codex", out)
        self.assertEqual(self.state()["tools"], ["codex"])
        self.assertFalse(self.rendered()[".claude/settings.json"])
        self.assertTrue(self.rendered()[".codex/config.toml"])
        self.assertTrue((self.root / ".flamin/inactive-adapters").exists())

    def test_copied_master_adapters_are_archived(self):
        from flaminlib.render import render_all
        render_all(self.root, "all")
        self.flamin("init", "--tool", "cursor")
        self.assertFalse((self.root / ".claude").exists())
        self.assertFalse((self.root / ".codex").exists())
        self.assertFalse((self.root / "CLAUDE.md").exists())
        self.assertTrue((self.root / ".cursor/agents").exists())
        self.assertTrue((self.root / "AGENTS.md").exists())
        self.assertTrue(list((self.root / ".flamin/inactive-adapters").rglob("config.toml")))

    def test_each_tool_has_only_its_own_discoverable_adapter(self):
        from flaminlib.render import render_all
        for tool in ("claude", "codex", "cursor"):
            with self.subTest(tool=tool):
                render_all(self.root, "all")
                if tool == "codex":
                    self.write(".codex/agents/orchestrator.toml", "name = 'orchestrator'\n")
                if tool == "cursor":
                    self.write(".cursor/agents/orchestrator.md", "---\nname: orchestrator\n---\n")
                if not (self.root / ".flamin/state.json").exists():
                    self.flamin("init", "--tool", tool)
                else:
                    self.flamin("init", "--tool", tool)
                    if self.last_request() in self.approvals()["requests"]:
                        rid = self.last_request()
                        if self.approvals()["requests"][rid]["status"] == "pending":
                            self.flamin("approve", rid, "--yes")
                            self.flamin("init", "--tool", tool)
                self.assertEqual(self.state()["tools"], [tool])
                for other, path in (("claude", ".claude"), ("codex", ".codex"), ("cursor", ".cursor")):
                    self.assertEqual((self.root / path).exists(), other == tool)
                self.assertFalse((self.root / ".codex/agents/orchestrator.toml").exists())
                self.assertFalse((self.root / ".cursor/agents/orchestrator.md").exists())

    def test_switch_retains_original_config_backup_and_custom_files(self):
        self.flamin("init", "--tool", "codex")
        self.write(".codex/config.toml", "# customized\n")
        self.write(".codex/agents/custom.toml", "name = 'custom'\n")
        self.flamin("init", "--tool", "claude")
        rid = self.last_request()
        self.flamin("approve", rid, "--yes")
        self.flamin("init", "--tool", "claude")
        self.assertFalse((self.root / ".codex").exists())
        self.flamin("init", "--tool", "codex")
        rid = self.last_request()
        self.flamin("approve", rid, "--yes")
        self.flamin("init", "--tool", "codex")
        self.assertIn("name = 'custom'", self.read(".codex/agents/custom.toml"))
        self.assertTrue(any(p.read_text(encoding="utf-8") == "# customized\n" for p in
                            (self.root / ".flamin/inactive-adapters").rglob("config.toml")))

    def test_same_tool_refresh_keeps_heartbeat(self):
        self.flamin("init", "--tool", "claude")
        beat = self.write(".flamin/tmp/heartbeat-claude", "recent")
        self.flamin("init")
        self.assertEqual(beat.read_text(encoding="utf-8"), "recent")

    def test_failed_switch_keeps_approval_and_restores_active_adapter(self):
        self.flamin("init", "--tool", "claude")
        self.flamin("init", "--tool", "codex")
        rid = self.last_request()
        self.flamin("approve", rid, "--yes")
        with mock.patch("flaminlib.render.render_all", side_effect=OSError("injected render failure")):
            with self.assertRaisesRegex(OSError, "injected render failure"):
                self.flamin("init", "--tool", "codex")
        self.assertEqual(self.state()["tools"], ["claude"])
        self.assertTrue((self.root / ".claude/settings.json").exists())
        self.assertFalse((self.root / ".codex").exists())
        self.assertNotIn("consumed", self.approvals()["requests"][rid])
        self.flamin("init", "--tool", "codex")
        self.assertEqual(self.state()["tools"], ["codex"])

    def test_failed_state_commit_restores_exact_discovery_files(self):
        self.flamin("init", "--tool", "claude")
        original = self.read(".claude/settings.json")
        self.flamin("init", "--tool", "cursor")
        rid = self.last_request()
        self.flamin("approve", rid, "--yes")
        with mock.patch.object(cmds, "record_tools", side_effect=OSError("injected state failure")):
            with self.assertRaisesRegex(OSError, "injected state failure"):
                self.flamin("init", "--tool", "cursor")
        self.assertEqual(self.read(".claude/settings.json"), original)
        self.assertFalse((self.root / ".cursor").exists())
        self.assertEqual(self.state()["tools"], ["claude"])
        self.assertNotIn("consumed", self.approvals()["requests"][rid])


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

    def test_legacy_product_requires_selection_when_ambiguous(self):
        self.flamin("init", "--tool", "claude")
        from flaminlib.render import render_all
        render_all(self.root, "codex")
        st = self.state()
        st.pop("tools")
        Store(self.root).save("state", st)  # a product made before state['tools'] existed
        out = self.flamin("init", expect=1)
        self.assertIn("ambiguous", out)
        out = self.flamin("init", "--tool", "codex")
        self.assertIn("Approval needed", out)
        self.assertTrue((self.root / ".claude").exists())

    def test_broken_claude_hook_is_reported(self):
        os.environ["CLAUDECODE"] = "1"
        self.flamin("init")
        s = json.loads(self.read(".claude/settings.local.json"))
        s["hooks"]["PreToolUse"][0]["hooks"][0]["args"][0] = "${CLAUDE_PROJECT_DIR}/kit/engine/missing.py"
        self.write(".claude/settings.local.json", json.dumps(s))
        out = self.flamin("doctor", expect=1)
        self.assertIn("FAIL Claude Code: .claude/settings.local.json differs", out)
        self.assertIn("FAIL Claude Code: the PreToolUse hook did not deny", out)

    def test_doctor_fix_cannot_switch(self):
        self.flamin("init", "--tool", "claude")
        out = self.flamin("doctor", "--fix", "--tool", "codex", expect=1)
        self.assertIn("cannot switch this product", out)
        self.assertEqual(self.state()["tools"], ["claude"])
        self.assertFalse((self.root / ".codex").exists())

    def test_doctor_reports_and_archives_inactive_files(self):
        self.flamin("init", "--tool", "claude")
        self.write(".codex/config.toml", "# unrelated local content\n")
        out = self.flamin("doctor", expect=1)
        self.assertIn("Inactive adapter remains discoverable: .codex", out)
        self.flamin("doctor", "--fix", expect=None)
        self.assertFalse((self.root / ".codex").exists())
        self.assertTrue(any(p.read_text(encoding="utf-8") == "# unrelated local content\n" for p in
                            (self.root / ".flamin/inactive-adapters").rglob("config.toml")))

    def test_repaired_hook_invalidates_old_heartbeat(self):
        self.flamin("init", "--tool", "claude")
        beat = self.write(".flamin/tmp/heartbeat-claude", "recent")
        self.write(".claude/settings.local.json", "{}\n")
        self.flamin("doctor", "--fix", expect=None)
        self.assertFalse(beat.exists())

    def test_doctor_probe_does_not_create_live_session_heartbeat(self):
        self.flamin("init", "--tool", "claude")
        beat = self.root / ".flamin/tmp/heartbeat-claude"
        beat.unlink(missing_ok=True)
        self.flamin("doctor", expect=None)
        self.assertFalse(beat.exists())

    def test_restored_missing_hook_invalidates_heartbeat(self):
        self.flamin("init", "--tool", "cursor")
        self.flamin("init", "--tool", "claude")
        self.flamin("approve", self.last_request(), "--yes")
        self.flamin("init", "--tool", "claude")
        self.flamin("init", "--tool", "cursor")
        self.flamin("approve", self.last_request(), "--yes")
        self.flamin("init", "--tool", "cursor")
        hook = self.root / ".cursor/hooks.json"
        hook.unlink()
        beat = self.write(".flamin/tmp/heartbeat-cursor", "old")
        self.flamin("doctor", "--fix", expect=None)
        self.assertTrue(hook.exists())
        self.assertFalse(beat.exists())

    def test_wrong_tool_hook_is_denied(self):
        self.flamin("init", "--tool", "claude")
        code, body, _ = self.hook("codex", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                            "tool_input": {"command": "pwd"}})
        self.assertIn("not this product's active AI tool", body)
        self.assertEqual(self.state()["tools"], ["claude"])

    def test_rejected_switch_keeps_files(self):
        self.flamin("init", "--tool", "claude")
        self.flamin("init", "--tool", "cursor")
        rid = self.last_request()
        self.flamin("approve", rid, "--no")
        self.flamin("init", "--tool", "cursor")
        self.assertTrue((self.root / ".claude/settings.json").exists())
        self.assertEqual(self.state()["tools"], ["claude"])


class TestRenderLineEndings(ToolProject):
    def test_crlf_checkout_is_current(self):
        self.flamin("init", "--tool", "claude")
        for rel in (".claude/settings.json", "CLAUDE.md"):
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
