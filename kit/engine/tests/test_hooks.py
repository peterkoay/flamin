"""PreToolUse, audit, gates and per-tool reply formats."""
import json

from helpers import Project

EXT_OLD = "        return None  # business rules for the Member aggregate, written once\n"
EXT_NEW = "        if not root.phone:\n            raise ValueError('phone')\n"


class HookBase(Project):
    def setUp(self):
        super().setUp()
        self.to_generated()
        self.flamin("develop", "member")


class TestClaudeHooks(HookBase):
    def test_fully_locked_edit_blocked(self):
        code, out, err = self.hook("claude", self.claude_edit("modules/member/services/base_register_service.py", "REQUIRED =", "REQ ="))
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("FULLY_LOCKED", err)

    def test_edit_inside_and_outside_extension(self):
        code, out, _ = self.hook("claude", self.claude_edit("modules/member/services/register_service.py", EXT_OLD, EXT_NEW))
        self.assertEqual((code, out), (0, ""))  # plain allow: no output
        code, _, err = self.hook("claude", self.claude_edit("modules/member/services/register_service.py",
                                                             "class RegisterService(BaseRegisterService):", "class RegisterService(object):"))
        self.assertEqual(code, 2)
        self.assertIn("outside every extension body", err)

    def test_direct_cross_module_import_blocked(self):
        code, _, err = self.hook("claude", self.claude_write("modules/member/services/x.py", "from modules.billing.services.pay import pay\n"))
        self.assertEqual(code, 2)
        self.assertIn("Cross-module calls go only through contracts", err)

    def test_destructive_and_secret(self):
        code, _, err = self.hook("claude", self.claude_bash("rm -rf /"))
        self.assertEqual(code, 2)
        self.assertIn("Destructive", err)
        key = "sk-proj-" + "Q" * 8 + "abcdEFGHijklMNOPqrst1234"
        code, _, err = self.hook("claude", self.claude_write("modules/member/services/cfg.py", f"KEY = '{key}'\n"))
        self.assertEqual(code, 2)
        self.assertIn("Secret leak", err)
        audit_text = json.dumps(self.audit_lines())
        self.assertNotIn(key, audit_text)
        self.assertIn("rm -rf /", audit_text)

    def test_enforcement_layer_and_state_blocked(self):
        for rel in ("kit/engine/flaminlib/policy.py", ".claude/settings.local.json", ".codex/hooks.json", "CLAUDE.md",
                    "flamin", ".flamin/approvals.json", ".flamin/state.json", ".flamin/stacks/python-fastapi/profile.json"):
            code, _, err = self.hook("claude", self.claude_write(rel, "x\n"))
            self.assertEqual(code, 2, rel)
        code, _, err = self.hook("claude", self.claude_bash("echo {} > .flamin/approvals.json"))
        self.assertEqual(code, 2)
        code, _, _ = self.hook("claude", self.claude_bash("sed -i 's/x/y/' kit/engine/flaminlib/hooks.py"))
        self.assertEqual(code, 2)

    def test_per_agent_paths(self):
        code, _, err = self.hook("claude", self.claude_write("modules/member/services/x.py", "x = 1\n", agent="tester"))
        self.assertEqual(code, 2)
        code, _, _ = self.hook("claude", self.claude_write("tests/test_x.py", "x = 1\n", agent="tester"))
        self.assertEqual(code, 0)
        code, _, err = self.hook("claude", self.claude_write("modules/member/services/x.py", "x = 1\n", agent=None))
        self.assertEqual(code, 2)  # the main session is the Orchestrator: never writes code
        code, _, _ = self.hook("claude", self.claude_write(".flamin/business/rules.md", "x\n", agent="business"))
        self.assertEqual(code, 0)
        code, _, _ = self.hook("claude", self.claude_write(".flamin/design/member/model.toml", "x\n", agent="designer"))
        self.assertEqual(code, 2)

    def test_launch_gate(self):
        base = {"hook_event_name": "PreToolUse", "session_id": "s", "permission_mode": "default", "tool_name": "Agent"}
        for name, want in (("orchestrator", 2), ("Explore", 2), ("planner", 2), ("developer", 0)):
            code, _, _ = self.hook("claude", dict(base, tool_input={"subagent_type": name}))
            self.assertEqual(code, want, name)
        code, _, err = self.hook("claude", dict(base, agent_id="a", agent_type="developer", tool_input={"subagent_type": "tester"}))
        self.assertEqual(code, 2)
        self.assertIn("depth", err)

    def test_ask_mode_rules(self):
        cmd = "./flamin approve R-0001 --yes"
        for mode, expect in (("default", "ask"), ("acceptEdits", "ask"), ("plan", "ask"), ("auto", "deny"), ("bypassPermissions", "deny"),
                             ("dontAsk", "deny"), ("weirdNewMode", "deny"), (None, "deny")):
            p = self.claude_bash(cmd, mode=mode)
            if mode is None:
                del p["permission_mode"]
            code, out, _ = self.hook("claude", p)
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], expect, mode)

    def test_ask_token_marks_human_prompt(self):
        self.write(".flamin/decisions/assumptions.md", "## A-001: x\n- Scope: product\n")
        self.hook("claude", self.claude_bash("./flamin approve A-001"))
        self.flamin("approve", "A-001")  # the command the human confirmed in the tool prompt
        self.assertEqual(self.approvals()["assumptions"]["A-001"]["approver"], "human via claude prompt")

    def test_gated_action_deny_then_terminal_then_retry(self):
        p = self.claude_bash("pip install requests", mode="bypassPermissions", agent="developer")
        code, out, err = self.hook("claude", p)
        self.assertEqual(code, 2)
        rid = err.split("(")[1].split(")")[0]
        code, _, _ = self.hook("claude", p)
        self.assertEqual(code, 2)  # still pending: no answer means no
        self.flamin("approve", rid, "--yes")
        code, _, _ = self.hook("claude", p)
        self.assertEqual(code, 0)  # the exact approved action, once
        code, _, _ = self.hook("claude", p)
        self.assertEqual(code, 2)  # consumed
        code, _, _ = self.hook("claude", self.claude_bash("pip install requests==2.0", mode="bypassPermissions"))
        self.assertEqual(code, 2)  # a different command needs its own approval

    def test_gated_action_ask_then_posttool_records_human(self):
        p = self.claude_bash("rm notes.txt", mode="default", agent="developer")
        code, out, _ = self.hook("claude", p)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "ask")
        post = dict(p, hook_event_name="PostToolUse", tool_response={"stdout": ""})
        self.hook("claude", post, event="posttool")
        reqs = [r for r in self.approvals()["requests"].values() if r["kind"] == "action"]
        self.assertEqual(reqs[-1]["status"], "approved")
        self.assertEqual(reqs[-1]["approver"], "human via claude prompt")

    def test_session_end_reminder(self):
        self.hook("claude", self.claude_edit("modules/member/services/register_service.py", EXT_OLD, EXT_NEW))
        post = dict(self.claude_edit("modules/member/services/register_service.py", EXT_OLD, EXT_NEW), hook_event_name="PostToolUse")
        self.hook("claude", post, event="posttool")
        code, out, _ = self.hook("claude", {"hook_event_name": "Stop", "session_id": "sess-1"}, event="sessionend")
        self.assertIn("reminder", json.loads(out)["systemMessage"])


class TestCodexHooks(HookBase):
    def test_apply_patch_locked(self):
        patch = ("*** Begin Patch\n*** Update File: modules/member/services/base_register_service.py\n@@\n"
                 "-REQUIRED = [\"phone\", \"password\"]\n+REQUIRED = []\n*** End Patch\n")
        p = {"hook_event_name": "PreToolUse", "session_id": "c1", "turn_id": "t", "permission_mode": "default",
             "agent_id": "a", "agent_type": "developer", "tool_name": "apply_patch", "tool_input": {"command": patch}}
        code, out, err = self.hook("codex", p)
        self.assertEqual(code, 0)  # JSON deny, exit 0 (PowerShell swallows exit 2 on Windows)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_codex_never_asks(self):
        p = {"hook_event_name": "PreToolUse", "session_id": "c1", "permission_mode": "default", "tool_name": "Bash",
             "tool_input": {"command": "./flamin approve R-0001 --yes"}}
        code, out, err = self.hook("codex", p)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("terminal", err)

    def test_spawn_and_agent_type(self):
        p = {"hook_event_name": "PreToolUse", "session_id": "c1", "tool_name": "collaborationspawn_agent",
             "tool_input": {"agent_type": "orchestrator", "task_name": "x", "message": "enc"}}

        def decision(payload):
            out = self.hook("codex", payload)[1]
            return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out.strip() else "allow"
        self.assertEqual(decision(p), "deny")
        p["tool_input"]["agent_type"] = "developer"
        self.assertEqual(decision(p), "allow")
        p2 = dict(p, agent_id="a", agent_type="developer")
        self.assertEqual(decision(p2), "deny")  # depth 1

    def test_stop_output_is_json(self):
        code, out, _ = self.hook("codex", {"hook_event_name": "Stop", "session_id": "c9", "turn_id": "t"}, event="sessionend")
        self.assertEqual(code, 0)
        json.loads(out)


class TestCursorHooks(HookBase):
    def cur(self, event, **kw):
        base = {"hook_event_name": event, "conversation_id": "conv-1", "generation_id": "g", "model": "m",
                "cursor_version": "3.21.18", "workspace_roots": [str(self.root)], "user_email": "person@example.invalid"}
        base.update(kw)
        return base

    def test_write_full_content_locked(self):
        rel = "modules/member/services/base_register_service.py"
        p = self.cur("preToolUse", tool_name="Write", tool_input={"file_path": self.abs(rel), "content": "x\n"})
        code, out, _ = self.hook("cursor", p)
        self.assertEqual(json.loads(out)["permission"], "deny")
        self.assertEqual(code, 0)
        self.assertNotIn("person@example.invalid", json.dumps(self.audit_lines()))
        self.assertEqual(self.audit_lines()[-1]["agent_source"], "lease")

    def test_allow_is_explicit_json(self):
        p = self.cur("preToolUse", tool_name="Read", tool_input={"file_path": self.abs("main.py")})
        code, out, _ = self.hook("cursor", p)
        self.assertEqual((code, json.loads(out)["permission"]), (0, "allow"))

    def test_unknown_tool_with_path_treated_as_write(self):
        rel = "modules/member/services/base_register_service.py"
        p = self.cur("preToolUse", tool_name="StrReplace", tool_input={"path": self.abs(rel), "old": "a", "new": "b"})
        self.assertEqual(json.loads(self.hook("cursor", p)[1])["permission"], "deny")

    def test_shell_gate_never_asks_without_mode(self):
        p = self.cur("beforeShellExecution", command="./flamin approve R-0001 --yes", cwd=str(self.root))
        self.assertEqual(json.loads(self.hook("cursor", p)[1])["permission"], "deny")

    def test_after_file_edit_reverts_breach(self):
        rel = "modules/member/services/base_register_service.py"
        before = self.read(rel)
        after = before.replace("REQUIRED =", "REQ =")
        self.write(rel, after)
        p = self.cur("afterFileEdit", file_path=self.abs(rel), edits=[{"old_string": "REQUIRED =", "new_string": "REQ ="}])
        self.hook("cursor", p, event="posttool")
        self.assertEqual(self.read(rel), before)
        self.assertEqual(self.audit_lines()[-1]["action"], "auto-revert")

    def test_subagent_depth_and_orchestrator(self):
        p = self.cur("subagentStart", subagent_id="sub-1", subagent_type="developer", parent_conversation_id="conv-1",
                     subagent_model="claude-sonnet-5", task="x")
        self.assertEqual(json.loads(self.hook("cursor", p)[1])["permission"], "allow")
        p2 = dict(p, subagent_id="sub-2", parent_conversation_id="sub-1")
        self.assertEqual(json.loads(self.hook("cursor", p2)[1])["permission"], "deny")
        p3 = dict(p, subagent_type="orchestrator")
        self.assertEqual(json.loads(self.hook("cursor", p3)[1])["permission"], "deny")

    def test_cursor_payload_through_claude_hook_file(self):
        rel = "modules/member/services/base_register_service.py"
        p = self.cur("preToolUse", tool_name="Write", tool_input={"file_path": self.abs(rel), "content": "x\n"})
        code, out, _ = self.hook("claude", p)  # Cursor may read Claude Code hook files; the engine answers in Cursor's format
        self.assertEqual(json.loads(out)["permission"], "deny")


class TestHookFailsClosed(Project):
    def test_internal_error_denies(self):
        self.init()
        (self.root / ".flamin/state.json").write_text("{broken", encoding="utf-8")
        code, out, _ = self.hook("claude", self.claude_write("x.py", "x\n"))
        self.assertEqual(code, 2)
        self.assertIn("hook error", out)
