"""Confirm and reopen Step 0 intake in a disposable workspace."""

import shutil

from helpers import Project

from flamin_intake_mcp import IntakeSession
from flaminlib import approvals, phase
from flaminlib.profiles import kit_profile
from flaminlib.product import Product


class IntakeMcpTests(Project):
    copy_kit = False

    def setUp(self):
        super().setUp()
        self.product = Product(self.root, tool="mcp")
        self.product.fdir.mkdir()
        self.product.save_state(phase.new_state("test"))
        self.session = IntakeSession(self.product)
        self.send("initialize", {"capabilities": {"elicitation": {}}})

    def send(self, method, params):
        return self.session.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})

    def propose(self, **answers):
        return self.send("tools/call", {"name": "flamin_save_intake", "arguments": {"answers": answers}})

    def decide(self, prompt, action="accept", confirmation="Save intake"):
        return self.session.handle({
            "jsonrpc": "2.0",
            "id": prompt["id"],
            "result": {"action": action, "content": {"confirmation": confirmation}},
        })

    def test_confirmation_persists_and_reopens(self):
        prompt = self.propose(name="Disposable pilot", purpose="Check persistence")
        self.assertEqual(prompt["method"], "elicitation/create")
        self.assertIn("Check persistence", prompt["params"]["message"])
        self.assertEqual(self.product.state()["intake"], {})

        result = self.decide(prompt)["result"]["structuredContent"]
        self.assertEqual(result["saved_fields"], {"name": "Disposable pilot", "purpose": "Check persistence"})
        self.assertEqual(len(result["missing_fields"]), 6)
        self.assertIsNone(self.decide(prompt))

        reopened = Product(self.root, tool="mcp")
        self.assertEqual(reopened.state()["intake"], result["saved_fields"])
        self.assertEqual(reopened.state()["product"], "Disposable pilot")
        self.assertEqual(reopened.state()["phase"], 0)
        self.assertEqual(reopened.store.checksum_problems(), [])
        self.assertFalse(reopened.store.path("approvals").exists())

    def test_cancel_wrong_confirmation_and_unsupported_host_leave_state_intact(self):
        self.assertTrue(self.decide(self.propose(name="Cancelled"), action="cancel")["result"]["isError"])
        self.assertTrue(self.decide(self.propose(name="Wrong"), confirmation="")["result"]["isError"])
        self.send("initialize", {"capabilities": {}})
        self.assertTrue(self.propose(name="Unsupported")["result"]["isError"])
        self.assertEqual(self.product.state()["intake"], {})

    def test_stale_confirmation_and_late_phase_are_rejected(self):
        prompt = self.propose(name="Stale")
        state = self.product.state()
        state["intake"]["purpose"] = "Concurrent update"
        self.product.save_state(state)
        self.assertTrue(self.decide(prompt)["result"]["isError"])
        self.assertEqual(self.product.state()["intake"], {"purpose": "Concurrent update"})

        state["phase"] = 1
        self.product.save_state(state)
        self.assertTrue(self.propose(name="Late")["result"]["isError"])

    def test_continue_lists_formal_decisions_without_writing_state(self):
        self.write(".flamin/decisions/assumptions.md", "## A-001: Demo scope\n- Scope: product\n- Answer: Web only\n")
        self.write(".flamin/sessions/last.md", "# Partial handoff\n")
        response = self.send("tools/call", {"name": "flamin_continue"})["result"]["structuredContent"]
        self.assertIn("Business", response["next_action"])
        self.assertFalse(response["latest_handoff"]["valid"])
        self.assertEqual(response["formal_decisions"], [{"id": "A-001", "kind": "assumption", "title": "Demo scope"}])

        request = {"name": "flamin_review_decision", "arguments": {"id": "A-001"}}
        original = self.send("tools/call", request)["result"]["structuredContent"]
        self.assertIn("Web only", original["payload"])
        self.write(".flamin/decisions/assumptions.md", "## A-001: Demo scope\n- Answer: Web and mobile\n")
        revised = self.send("tools/call", request)["result"]["structuredContent"]
        self.assertNotEqual(original["payload_hash"], revised["payload_hash"])
        self.assertTrue(revised["confirmation_available"])
        self.assertIn("error", self.send("tools/call", {"name": "flamin_approve", "arguments": {"id": "A-001"}}))
        self.assertFalse(self.product.store.path("approvals").exists())

    def confirm_decision(self, rid, decision="approve"):
        return self.send("tools/call", {"name": "flamin_confirm_decision",
                                        "arguments": {"id": rid, "decision": decision}})

    def test_assumption_requires_a_current_single_use_confirmation(self):
        path = ".flamin/decisions/assumptions.md"
        self.write(path, "## A-001: Demo scope\n- Answer: Web only\n")
        prompt = self.confirm_decision("A-001")
        self.assertIn("Web only", prompt["params"]["message"])
        self.assertTrue(self.decide(prompt, action="cancel")["result"]["isError"])
        stale = self.confirm_decision("A-001")
        self.write(path, "## A-001: Demo scope\n- Answer: Web and mobile\n")
        self.assertTrue(self.decide(stale, confirmation="Approve A-001")["result"]["isError"])
        self.assertFalse(self.product.store.path("approvals").exists())
        current = self.confirm_decision("A-001")
        self.assertIn("structuredContent", self.decide(current, confirmation="Approve A-001")["result"])
        self.assertIsNone(self.decide(current, confirmation="Approve A-001"))
        self.assertEqual(approvals.load(self.product.store)["assumptions"]["A-001"]["status"], "agreed")

    def test_confirmed_stack_approval_advances_workflow(self):
        profile = kit_profile("minimal")
        shutil.copytree(profile.folder, self.root / "kit" / "stacks" / profile.name)
        answers = {key: "Demo " + key for key in phase.MANDATORY_KEYS}
        self.decide(self.propose(**answers))
        self.write(".flamin/decisions/stack.md", "Use the shipped minimal profile.")
        status = self.send("tools/call", {"name": "flamin_continue"})["result"]["structuredContent"]
        proposed = self.send("tools/call", {"name": "flamin_propose_stack", "arguments": {
            "profile": profile.name, "expected_revision": status["intake_revision"],
        }})["result"]["structuredContent"]
        rid = proposed["id"]
        prompt = self.confirm_decision(rid)
        self.assertIn("Use the shipped minimal profile.", prompt["params"]["message"])
        self.assertFalse(self.product.state()["stack_approved"])
        self.assertIn("structuredContent", self.decide(prompt, confirmation=f"Approve {rid}")["result"])
        status = self.send("tools/call", {"name": "flamin_continue"})["result"]["structuredContent"]
        self.assertEqual(status["step"], "Step 1 ready")
        self.assertEqual(status["recorded_human_decisions"], [{"id": rid, "kind": "stack", "outcome": "approved"}])

class WorkflowMcpTests(Project):
    def test_later_steps_use_the_engine_and_human_decisions(self):
        from helpers import INTAKE, MODEL, WRITE_SPEC

        product = Product(self.root, tool="mcp")
        session = IntakeSession(product)
        session.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"capabilities": {"elicitation": {}}}})

        def send(name, **args):
            return session.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                   "params": {"name": "flamin_" + name, "arguments": args}})

        def call(name, **args):
            return send(name, **args)["result"]

        def run(name, **args):
            result = call(name, **args)
            self.assertFalse(result.get("isError"), result)
            return result["structuredContent"]

        def confirm(prompt, phrase):
            self.assertEqual(prompt.get("method"), "elicitation/create", prompt)
            answer = session.handle({"jsonrpc": "2.0", "id": prompt["id"], "result": {
                "action": "accept", "content": {"confirmation": phrase},
            }})["result"]
            self.assertFalse(answer.get("isError"), answer)

        def approve(expected_text):
            rid = self.last_request()
            prompt = send("confirm_decision", id=rid, decision="approve")
            self.assertIn(expected_text, prompt["params"]["message"])
            confirm(prompt, "Approve " + rid)

        confirm(send("initialize"), "Initialize workspace")
        self.assertFalse((self.root / ".git" / "hooks" / "pre-commit").exists())
        confirm(send("save_intake", answers=dict(item.split("=", 1) for item in INTAKE)), "Save intake")
        self.write(".flamin/decisions/stack.md", "Use python-fastapi for the example.")
        run("propose_stack", profile="python-fastapi", expected_revision=run("continue")["intake_revision"])
        approve("Use python-fastapi")

        self.assertTrue(call("record_analysis", slug="../member")["isError"])
        self.assertTrue(call("record_integration_test", result="pass")["isError"])
        self.write(".flamin/analysis/member/requirements.md", "R1 register.\n")
        run("record_analysis", slug="member")
        run("add_module", add_module="member")
        run("complete_model_step", step=2)
        self.write(".flamin/design/member/model.toml", MODEL)
        run("complete_model_step", step=3)
        run("complete_model_step", step=4)
        run("propose_baseline")
        stale = self.last_request()
        self.write(".flamin/design/member/model.toml", MODEL + "\n# Reviewed update\n")
        self.assertTrue(call("review_decision", id=stale)["isError"])
        run("propose_baseline")
        approve("Reviewed update")
        self.write(".flamin/analysis/member/plan.md", "## Batch 1\n- POST /api/member/register\n")
        run("propose_plan", module="member")
        approve("POST /api/member/register")
        self.write(".flamin/design/member/register.toml", WRITE_SPEC)
        run("record_design", module="member", spec="register")
        run("generate", module="member", spec="register")
        run("lease_module", module="member")
        run("complete_development", module="member")
        run("record_wiring", module="member", done=True)
        run("record_module_test", module="member", result="pass")
        self.assertIn("Step 11", run("continue")["next_action"])
        run("record_integration_test", result="pass")
        run("review_architecture")
        run("propose_release")
        self.assertIsNone(self.state()["versions"]["1"]["released"])
        approve("machine_version")
        self.assertTrue(self.state()["versions"]["1"]["released"])
        run("raise_change_request", title="Improve registration")
        run("propose_change_list", version=2, cr=["CR-001=member@8"])
        approve("Improve registration")
        self.assertEqual(self.state()["modules"]["member"]["steps_done"], [6, 7])
