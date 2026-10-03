"""Review round 5 (D-38 to D-46): alignment tests."""
import json
import os
import tomllib

from helpers import Project

from flaminlib import cmds, manifest as mf, policy as pol, render
from flaminlib.util import sha256_text


def bash(command, agent=None, tool_use_id=None, mode="default"):
    p = {"hook_event_name": "PreToolUse", "session_id": "s", "permission_mode": mode, "tool_name": "Bash",
         "tool_input": {"command": command}}
    if agent:
        p.update({"agent_id": "a-1", "agent_type": agent})
    if tool_use_id:
        p["tool_use_id"] = tool_use_id
    return p


class TestRenderRound5(Project):
    def test_codex_shell_environment_preserves_agents_and_hooks(self):
        self.init("codex")
        cfg = tomllib.loads(self.read(".codex/config.toml"))
        self.assertEqual(cfg["shell_environment_policy"], {
            "inherit": "core", "set": {
                "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1",
                "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "10"}})
        self.assertEqual(cfg["agents"], {"max_depth": 1, "max_concurrent_threads_per_session": 10})
        self.assertEqual(cfg["hooks"], tomllib.loads(render.codex_hooks_toml())["hooks"])
        self.assertEqual(set(cfg["hooks"]), {"PreToolUse", "SubagentStart", "PostToolUse", "Stop", "SessionEnd"})
        workers = {p.stem for p in (self.root / ".codex/agents").glob("*.toml")}
        self.assertEqual(workers, {"business", "analyst", "architect", "planner", "designer", "developer", "tester"})

    def test_codex_keys_and_cursor_command(self):
        self.init("codex")
        cfg = tomllib.loads(self.read(".codex/config.toml"))
        self.assertEqual(cfg["agents"], {"max_depth": 1, "max_concurrent_threads_per_session": 10})  # D-38
        self.assertIn("PreToolUse", cfg["hooks"])  # D-39: hooks inline only
        self.assertFalse((self.root / ".codex/hooks.json").exists())
        cur = json.loads(render.cursor_local()[".cursor/hooks.json"])["hooks"]["preToolUse"][0]["command"]
        self.assertNotIn("LASTEXITCODE", cur)  # D-40: Cursor keeps the plain launcher
        self.assertIn(".flamin-kit-maintenance", self.read(".gitignore"))


class TestShellByAgentsWithoutShell(Project):  # D-41
    def test_claude_and_codex(self):
        self.init()
        for agent in ("business", "analyst", "architect", "planner"):
            code, out, _ = self.hook("claude", bash("ls", agent=agent))
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny", agent)
            out = self.hook("codex", bash("ls", agent=agent))[1]
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny", agent)
        for agent in ("designer", "developer", "tester", None):
            code, out, _ = self.hook("claude", bash("ls", agent=agent))
            self.assertEqual((code, out), (0, ""), agent)


class TestIdempotency(Project):  # D-44
    def setUp(self):
        super().setUp()
        self.init()

    def test_one_audit_line_per_tool_call(self):
        self.flamin("init", "--tool", "cursor")
        self.flamin("approve", self.last_request(), "--yes")
        self.flamin("init", "--tool", "cursor")
        cur = {"hook_event_name": "preToolUse", "conversation_id": "c", "generation_id": "g1", "cursor_version": "3",
               "tool_name": "Shell", "tool_input": {"command": "ls"}, "tool_use_id": "tu-1", "user_email": "x@y.z"}
        self.hook("cursor", cur)
        self.hook("claude", cur)  # the same call arriving through Claude Code's hook file
        lines = [e for e in self.audit_lines() if e.get("tool_name") == "Shell"]
        self.assertEqual(len(lines), 1)
        self.hook("cursor", dict(cur, tool_use_id="tu-2"))
        self.assertEqual(len([e for e in self.audit_lines() if e.get("tool_name") == "Shell"]), 2)

    def test_key_without_tool_use_id(self):
        from flaminlib.hooks import call_key
        a = call_key("preToolUse", {"generation_id": "g", "tool_input": {"command": "ls"}})
        b = call_key("preToolUse", {"generation_id": "g", "tool_input": {"command": "pwd"}})
        self.assertNotEqual(a, b)
        self.assertEqual(a, call_key("preToolUse", {"generation_id": "g", "tool_input": {"command": "ls"}}))

    def test_approved_action_survives_a_repeat_of_the_same_call(self):
        p = bash("pip install requests", agent="developer", tool_use_id="tu-9", mode="bypassPermissions")
        out = self.hook("claude", p)[2]
        rid = out.split("(")[1].split(")")[0]
        self.assertEqual(len([r for r in self.approvals()["requests"].values() if r["kind"] == "action"]), 1)
        self.hook("claude", p)  # repeat while pending reuses the open request
        self.assertEqual(len([r for r in self.approvals()["requests"].values() if r["kind"] == "action"]), 1)
        self.flamin("approve", rid, "--yes")
        self.assertEqual(self.hook("claude", p)[0], 0)
        self.assertEqual(self.hook("claude", p)[0], 0)  # same tool call again: still allowed
        self.assertEqual(self.hook("claude", dict(p, tool_use_id="tu-10"))[0], 2)  # a new call needs a new answer


class TestGitattributes(Project):  # D-45
    def test_enforcement_and_exec_bits(self):
        self.assertTrue(pol.is_enforcement_path(".gitattributes"))
        self.init()
        code, _, _ = self.hook("claude", self.claude_write(".gitattributes", "* -text\n", agent="developer"))
        self.assertEqual(code, 2)
        self.git("add", "flamin")
        self.git("update-index", "--chmod=-x", "flamin")
        problems = []
        cmds.exec_bit_check(self.root, problems)
        self.assertTrue(any("flamin" in p and "executable" in p for p in problems))
        self.git("update-index", "--chmod=+x", "flamin")
        problems = []
        cmds.exec_bit_check(self.root, problems)
        self.assertEqual(problems, [])


class TestMaintenanceMode(Project):  # D-46
    def test_switch_is_human_only(self):
        for cmd in ("./flamin kit-maintenance on", ".\\flamin.cmd kit-maintenance off", "flamin doctor --kit --update-manifest"):
            self.assertIsNotNone(pol.human_only(cmd), cmd)
        self.assertIsNone(pol.human_only("./flamin kit-maintenance test"))

    def test_mode_opens_kit_files_only(self):
        self.git("init", "-q")
        code, _, _ = self.hook("claude", self.claude_write("kit/rules/core.md", "x\n", agent="developer"))
        self.assertEqual(code, 2)  # mode off: kit is enforcement layer
        self.assertIn("ON", self.flamin("kit-maintenance", "on"))
        self.assertIn("maintenance mode is ON", self.flamin("status"))
        code, _, _ = self.hook("claude", self.claude_write("kit/rules/core.md", "x\n", agent="developer"))
        self.assertEqual(code, 0)
        for shut in (".claude/settings.json", "CLAUDE.md", ".gitattributes", ".git/config"):
            code, _, _ = self.hook("claude", self.claude_write(shut, "x\n", agent="developer"))
            self.assertEqual(code, 2, shut)
        out = self.flamin("init", expect=1)
        self.assertIn("maintenance mode is on", out)
        self.assertIn("kit-maintenance flag is set", self.flamin("doctor", "--kit", expect=1))
        self.flamin("kit-maintenance", "off")
        self.assertIn("off", self.flamin("kit-maintenance", "status"))

    def test_mode_refused_in_a_product(self):
        self.init()
        self.flamin("kit-maintenance", "on", expect=1)

    def test_untested_kit_change_refused_at_commit(self):
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        (self.root / "kit" / "MANIFEST").write_text(mf.build(self.root), encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base", "--no-verify")
        self.flamin("kit-maintenance", "on")
        p = self.root / "kit" / "rules" / "core.md"
        p.write_text(p.read_text(encoding="utf-8") + "\nmore\n", encoding="utf-8")
        text = mf.build(self.root)
        (self.root / "kit" / "MANIFEST").write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.assertIn("kit-maintenance test", self.flamin("check-staged", expect=1))
        flag = self.root / pol.MAINTENANCE_FLAG
        data = json.loads(flag.read_text(encoding="utf-8"))
        data["tested_manifest"] = sha256_text(text)  # what `flamin kit-maintenance test` records after a pass
        flag.write_text(json.dumps(data), encoding="utf-8")
        self.flamin("check-staged")
