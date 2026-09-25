"""Intake, phases, gates, assumptions, versions, change requests, idempotency, handoff, resume."""
import json

from helpers import INTAKE, Project


class TestIntakeAndAssumptions(Project):
    def test_halt_on_missing_mandatory_item(self):
        self.init()
        out = self.flamin("intake", "--check", expect=2)
        self.assertIn("HALT", out)
        args = []
        for kv in INTAKE:
            if not kv.startswith("success="):
                args += ["--set", kv]
        out = self.flamin("intake", *args)
        self.assertIn("Success criteria", out)
        self.flamin("intake", "--check", expect=2)
        self.write(".flamin/analysis/member/requirements.md", "x\n")
        out = self.flamin("analyze", "member", expect=1)
        self.assertIn("intake is not complete", out)

    def test_assumption_blocked_until_agreed(self):
        self.to_stack()
        self.write(".flamin/analysis/member/requirements.md", "x\n")
        with open(self.root / ".flamin/decisions/assumptions.md", "a", encoding="utf-8") as fh:
            fh.write("\n## A-001: phone is unique\n- Scope: member\n- Answer (exact words): \n- Status: agreed\n")
        out = self.flamin("analyze", "member", expect=1)  # "agreed" written by an agent in the notes does not count
        self.assertIn("A-001", out)
        self.assertEqual(self.approvals()["assumptions"]["A-001"]["status"], "open")
        # deleting the block from the notes does not close it either
        self.write(".flamin/decisions/assumptions.md", "# notes\n")
        self.flamin("analyze", "member", expect=1)
        self.flamin("approve", "A-001", "--yes")
        self.flamin("analyze", "member")
        a = self.approvals()["assumptions"]["A-001"]
        self.assertEqual((a["status"], a["approver"]), ("agreed", "human via terminal"))

    def test_stack_needs_recommendation_and_gate(self):
        self.to_intake_done()
        self.flamin("intake", "--stack", "python-fastapi", expect=1)
        self.write(".flamin/decisions/stack.md", "because\n")
        self.flamin("intake", "--stack", "python-fastapi")
        self.assertFalse(self.state()["stack_approved"])
        self.flamin("approve", self.last_request(), "--yes")
        self.assertTrue(self.state()["stack_approved"])
        self.assertTrue((self.root / ".flamin/stacks/python-fastapi/profile.json").exists())

    def test_no_answer_means_no(self):
        self.to_intake_done()
        self.write(".flamin/decisions/stack.md", "because\n")
        self.flamin("intake", "--stack", "python-fastapi")
        out = self.flamin("approve", self.last_request(), expect=1)  # no --yes and no terminal prompt
        self.assertIn("needs a human answer", out)
        self.assertFalse(self.state()["stack_approved"])

    def test_approval_covers_exact_payload(self):
        self.to_intake_done()
        self.write(".flamin/decisions/stack.md", "because\n")
        self.flamin("intake", "--stack", "python-fastapi")
        rid = self.last_request()
        self.write(".flamin/decisions/stack.md", "changed reasons\n")
        self.flamin("intake", "--stack", "python-fastapi")
        self.assertNotEqual(self.last_request(), rid)  # a changed payload is a new request


class TestPhases(Project):
    def test_phase2_step_before_baseline_is_refused(self):
        self.to_stack()
        self.write(".flamin/analysis/member/requirements.md", "x\n")
        self.flamin("analyze", "member")
        self.flamin("model", "--add-module", "member")
        out = self.flamin("plan", "member", expect=1)
        self.assertIn("baseline", out)
        self.flamin("model", "--step", "3", expect=1)  # step order

    def test_cycle_refused(self):
        self.to_stack()
        self.write(".flamin/analysis/x/requirements.md", "x\n")
        self.flamin("analyze", "x")
        self.flamin("model", "--add-module", "a", "--depends", "b")
        out = self.flamin("model", "--add-module", "b", "--depends", "a", expect=1)
        self.assertIn("cycle", out)

    def test_full_v1_then_idempotent(self):
        self.to_generated()
        st = self.state()
        self.assertEqual(st["modules"]["member"]["steps_done"], [6, 7])
        self.assertIn("No change needed", self.flamin("generate", ".flamin/design/member/register.toml"))
        self.flamin("develop", "member")
        self.flamin("develop", "member", "--done")
        self.flamin("wire", "member", "--done")
        self.flamin("module-done", "member", "--result", "pass")
        self.assertIn("No change needed", self.flamin("module-done", "member", "--result", "pass"))
        self.flamin("integration-test", "--result", "pass")
        self.flamin("architecture-review")
        self.flamin("release")
        self.assertIn("No change needed", self.flamin("release"))
        self.assertEqual(self.state()["build"], 1)

    def test_module_done_refuses_locked_edit(self):
        self.to_generated()
        self.flamin("develop", "member")
        self.flamin("develop", "member", "--done")
        self.flamin("wire", "member", "--done")
        p = self.root / "modules/member/services/base_register_service.py"
        p.write_text(p.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
        out = self.flamin("module-done", "member", "--result", "pass", expect=1)
        self.assertIn("FULLY_LOCKED", out)

    def test_lease_cap(self):
        from flaminlib import phase
        self.assertEqual(phase.MAX_LEASES, 10)


class TestVersions(Project):
    def _finish_v(self):
        self.flamin("develop", "member")
        self.flamin("develop", "member", "--done")
        self.flamin("wire", "member", "--done")
        self.flamin("module-done", "member", "--result", "pass")
        self.flamin("integration-test", "--result", "pass")
        self.flamin("architecture-review")

    def test_v1_rejected_then_v2_released(self):
        self.to_generated()
        self._finish_v()
        self.flamin("release")
        r1 = self.last_request()
        self.assertEqual(self.state()["build"], 1)
        self.flamin("approve", r1, "--no")
        st = self.state()
        self.assertEqual(st["versions"]["1"]["released"], False)
        self.assertIn("Released: no", self.read(".flamin/versions/v1.md"))
        # v2 through a CR list
        self.flamin("cr-new", "cancel membership")
        st = self.state()
        self.assertEqual(st["version"], 2)
        self.assertEqual(st["crs"][0]["version"], 2)
        self.flamin("develop", "member", expect=1)  # CR list not approved yet
        self.flamin("cr-approve", "v2", "--cr", "CR-001=member@8")
        self.flamin("approve", self.last_request(), "--yes")
        st = self.state()
        self.assertEqual(st["modules"]["member"]["steps_done"], [6, 7])  # reopened only from Step 8
        # a CR raised after cr-approve lands in the next version
        self.flamin("cr-new", "export members")
        self.assertEqual([c["version"] for c in self.state()["crs"]], [2, 3])
        self._finish_v()
        self.flamin("release")
        self.assertEqual(self.state()["build"], 2)  # exactly +1 per release candidate
        self.flamin("approve", self.last_request(), "--yes")
        st = self.state()
        self.assertTrue(st["versions"]["2"]["released"])
        self.assertIn("Released: yes", self.read(".flamin/versions/v2.md"))
        text = json.dumps(st) + self.read(".flamin/versions/v1.md") + self.read(".flamin/versions/v2.md")
        self.assertNotRegex(text, r"\bD-?\d+\b|\bD cycle")

    def test_cr_rework_only_affected_modules(self):
        self.to_generated()
        self._finish_v()
        st_before = self.state()
        self.flamin("release")
        self.flamin("approve", self.last_request(), "--yes")
        self.flamin("cr-new", "x")
        out = self.flamin("cr-approve", "v2", "--cr", "CR-001=member@4", expect=1)
        self.assertIn("baseline-amend", out)
        self.flamin("cr-approve", "v2", "--cr", "CR-001=member@10")
        self.flamin("approve", self.last_request(), "--yes")
        self.assertEqual(self.state()["modules"]["member"]["steps_done"], [6, 7, 8, 9])
        self.assertEqual(st_before["modules"]["member"]["steps_done"], [6, 7, 8, 9, 10])


class TestHandoffResume(Project):
    def test_handoff_validation(self):
        self.init()
        good = "## Objective\nx\n\n## State Ledger\n- Completed: y\n\n## Artifact References\n- Changed: z\n"
        self.write(".flamin/sessions/good.md", good)
        self.flamin("handoff", "--validate", ".flamin/sessions/good.md")
        for missing in ("Objective", "State Ledger", "Artifact References"):
            bad = good.replace(f"## {missing}", "## Other")
            self.write(".flamin/sessions/bad.md", bad)
            out = self.flamin("handoff", "--validate", ".flamin/sessions/bad.md", expect=1)
            self.assertIn(missing, out)
        self.write(".flamin/sessions/empty.md", "## Objective\n\n## State Ledger\n- a\n## Artifact References\n- b\n")
        self.flamin("handoff", "--validate", ".flamin/sessions/empty.md", expect=1)
        self.flamin("handoff", "--stdin", stdin=good)
        self.flamin("handoff", "--stdin", stdin="## Objective\nx\n", expect=1)

    def test_resume_reads_ledger(self):
        self.to_stack()
        out = self.flamin("resume")
        self.assertIn("Step 0 stack approved", out)
        self.assertIn("Next: Step 1", out)


class TestInitStatus(Project):
    def test_init_twice_changes_nothing_and_refuses_new_product(self):
        self.init()
        before = self.read(".flamin/state.json")
        out = self.flamin("init", "--tool", "claude")
        self.assertIn("already current", out)
        self.assertEqual(before, self.read(".flamin/state.json"))
        self.flamin("init", "--new-product", expect=1)

    def test_status_warns_when_hooks_not_running(self):
        self.init()
        out = self.flamin("status")
        self.assertIn("flamin hooks are not running in this session", out)
        self.hook("claude", {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "./flamin status"},
                             "permission_mode": "default", "session_id": "s"})
        out = self.flamin("status")
        self.assertNotIn("hooks are not running", out)

    def test_status_without_product(self):
        out = self.flamin("status")
        self.assertIn("No product here yet", out)
