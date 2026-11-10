"""Cryptographic decision coverage with a disposable authenticator fixture."""

import hashlib
import http.client
import json
from http.server import HTTPServer
from threading import Thread

from helpers import Project

from flamin_decision_screen import handler_for
from flamin_intake_mcp import IntakeSession
from flamin_status_mcp import continue_workspace, review_decision
from flaminlib import approvals, phase
from flaminlib.decision_desk import DecisionDesk, encode
from flaminlib.product import Product
from flaminlib.util import FlaminError

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
except ImportError:
    ec = None


class DecisionDeskTests(Project):
    copy_kit = False
    origin = "http://localhost:9421"
    credential = encode(b"rehearsal credential id")
    notes = ".flamin/decisions/assumptions.md"

    def setUp(self):
        if ec is None:
            self.skipTest("Install the optional decision desk cryptography dependency.")
        super().setUp()
        self.product = Product(self.root)
        self.product.fdir.mkdir()
        self.product.save_state(phase.new_state("test"))
        self.write(self.notes, "## A-001: Demo scope\n- Answer: Web only.\n")
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.public = self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.database = self.tmp / "desk.db"
        self.desk = self.open_desk()

    def open_desk(self):
        return DecisionDesk(self.database, self.root, self.origin, self.credential, self.public,
                            lambda rid: review_decision(self.product, rid, protected=True))

    def prepare(self, rid="A-001", decision="approve"):
        return self.desk.prepare(rid, decision, self.desk.inspect(rid))["challenge"]

    def assertion(self, challenge, *, key=None, flags=5, origin=None, rp="localhost"):
        client = json.dumps({"type": "webauthn.get", "challenge": challenge,
                             "origin": origin or self.origin, "crossOrigin": False}).encode()
        auth = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + bytes(4)
        proof = (key or self.key).sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": self.credential, "client_data": encode(client), "authenticator_data": encode(auth),
                "signature": encode(proof)}

    def test_verified_decision_persists_and_replay_is_rejected(self):
        challenge = self.prepare()
        proof = self.assertion(challenge)
        result = self.desk.decide(challenge, proof)
        self.assertEqual(result["outcome"], "agreed")
        with self.assertRaisesRegex(FlaminError, "already used"):
            self.desk.decide(challenge, proof)
        with self.assertRaisesRegex(FlaminError, "already has a recorded"):
            self.open_desk().prepare("A-001", "reject", self.desk.inspect("A-001"))
        status = continue_workspace(self.product, self.database)
        self.assertFalse(status["formal_decisions"])
        self.assertEqual(status["recorded_human_decisions"][0]["id"], "A-001")
        self.assertFalse(self.product.store.path("approvals").exists())
        self.write(self.notes, "## A-001: Demo scope\n- Answer: Web and mobile.\n")
        self.assertEqual(continue_workspace(self.product, self.database)["formal_decisions"][0]["id"], "A-001")

    def test_forged_wrong_origin_unverified_and_stale_proofs_fail(self):
        challenge = self.prepare()
        variants = [{"key": ec.generate_private_key(ec.SECP256R1())}, {"flags": 1},
                    {"origin": "http://example.invalid"}, {"rp": "example.invalid"}]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaisesRegex(FlaminError, "did not match"):
                self.desk.decide(challenge, self.assertion(challenge, **variant))
        self.write(self.notes, "## A-001: Demo scope\n- Answer: Web and mobile.\n")
        with self.assertRaisesRegex(FlaminError, "proposal changed"):
            self.desk.decide(challenge, self.assertion(challenge))
        self.assertFalse(continue_workspace(self.product, self.database)["recorded_human_decisions"])

    def test_expired_challenge_and_cross_choice_are_rejected(self):
        approve = self.prepare()
        reject = self.prepare(decision="reject")
        with self.assertRaisesRegex(FlaminError, "did not match"):
            self.desk.decide(reject, self.assertion(approve))
        proposal, decision, _ = self.desk.pending[approve]
        self.desk.pending[approve] = (proposal, decision, 0)
        with self.assertRaisesRegex(FlaminError, "expired"):
            self.desk.decide(approve, self.assertion(approve))
        self.assertEqual(self.desk.decide(reject, self.assertion(reject))["outcome"], "rejected")

    def test_stack_proposal_uses_recommendation_and_remains_separate_from_application(self):
        state = self.product.state()
        state["intake"] = {field: f"fixture {field}" for field, _ in phase.MANDATORY}
        self.product.save_state(state)
        self.write(".flamin/decisions/stack.md", "Use the shipped minimal profile.")
        status = continue_workspace(self.product, self.database)
        session = IntakeSession(self.product, self.database)
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "flamin_propose_stack",
            "arguments": {"profile": "minimal", "expected_revision": status["intake_revision"]},
        }}
        proposal = session.handle(request)["result"]["structuredContent"]
        self.assertIn("Use the shipped minimal profile.", proposal["payload"])
        self.assertTrue(session.handle(request)["result"]["isError"])
        challenge = self.prepare(proposal["id"])
        self.assertEqual(self.desk.decide(challenge, self.assertion(challenge))["outcome"], "agreed")
        status = continue_workspace(self.product, self.database)
        self.assertEqual(status["step"], "Step 0 stack choice")
        self.assertEqual(status["recorded_human_decisions"][0]["kind"], "stack")
        self.assertEqual(approvals.load(self.product.store)["requests"][proposal["id"]]["status"], "pending")

    def test_loopback_screen_checks_origin_and_records_signed_choice(self):
        with HTTPServer(("127.0.0.1", 0), None) as server:
            port = server.server_port
            self.origin = self.desk.origin = f"http://localhost:{port}"
            handler = handler_for(self.desk, self.product, port)
            server.RequestHandlerClass = handler
            worker = Thread(target=server.serve_forever)
            worker.start()
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            headers = {"Host": f"localhost:{port}", "Origin": self.origin, "Content-Type": "application/json"}

            def post(path, body, request_headers=headers):
                connection.request("POST", path, json.dumps(body), request_headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())

            try:
                connection.request("GET", "/", headers=headers)
                page = connection.getresponse()
                self.assertEqual(page.status, 200)
                self.assertIn(b"Approve with passkey", page.read())
                self.assertEqual(post("/review", {"id": "A-001"}, {**headers, "Origin": "http://example.invalid"})[0],
                                 403)
                _, proposal = post("/review", {"id": "A-001"})
                _, prepared = post("/prepare", {"id": "A-001", "decision": "approve", "reviewed": proposal})
                challenge = prepared["challenge"]
                payload = {"challenge": challenge, "assertion": self.assertion(challenge)}
                self.assertEqual(post("/decide", payload)[1]["outcome"], "agreed")
                self.assertEqual(post("/decide", payload)[0], 409)
            finally:
                connection.close()
                server.shutdown()
                worker.join()
