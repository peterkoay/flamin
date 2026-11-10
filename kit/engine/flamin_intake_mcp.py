"""Workspace MCP server for the FLAMIN product workflow."""

from __future__ import annotations

import json
import sys
from functools import partial
from pathlib import Path
from uuid import uuid4

sys.dont_write_bytecode = True

from flamin_status_mcp import READ_TOOLS, dispatch, review_decision, rpc_error, tool_error  # noqa: E402
from flamin_workflow_mcp import (  # noqa: E402
    OPERATIONS, check_new_workspace, initialize_workspace, run_workflow, workflow_tools,
)
from flaminlib import flow, phase  # noqa: E402
from flaminlib.product import Product  # noqa: E402
from flaminlib.profiles import kit_profiles  # noqa: E402
from flaminlib.util import FlaminError  # noqa: E402

INTAKE_FIELDS = dict(phase.MANDATORY)
INIT_TOOL = {
    "name": "flamin_initialize",
    "description": "Ask the human to initialize a new product in the paired FLAMIN kit folder.",
    "inputSchema": {"type": "object", "additionalProperties": False},
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
}
INTAKE_TOOL = {
    "name": "flamin_save_intake",
    "description": "Show proposed Step 0 answers to the user and save only after they confirm in the form.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "answers": {
                "type": "object",
                "properties": {key: {"type": "string", "description": label} for key, label in phase.MANDATORY},
                "minProperties": 1,
                "additionalProperties": False,
            },
        },
        "required": ["answers"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
}
DECISION_TOOL = {
    "name": "flamin_confirm_decision",
    "description": "Ask the human to approve or reject a workflow proposal in the host form.",
    "inputSchema": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "decision": {"enum": ["approve", "reject"]}},
        "required": ["id", "decision"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
}
STACK_TOOL = {
    "name": "flamin_propose_stack",
    "description": "Open a formal Step 0 proposal for a shipped stack profile. Does not decide or apply the stack.",
    "inputSchema": {
        "type": "object",
        "properties": {"profile": {"type": "string", "enum": kit_profiles()},
                       "expected_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
        "required": ["profile", "expected_revision"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
}


def propose_stack(product: Product, args: object) -> dict:
    if (not isinstance(args, dict) or set(args) != {"profile", "expected_revision"} or
            args["profile"] not in kit_profiles() or not isinstance(args["expected_revision"], str)):
        raise FlaminError("Choose one shipped profile and supply the current intake revision.")
    profile = args["profile"]
    if (product.fdir / "decisions" / "proposed-stacks" / profile / "profile.json").exists():
        raise FlaminError("A local proposal shadows this shipped profile.")
    request, _ = flow.propose_stack(product, [profile], args["expected_revision"])
    return review_decision(product, request["id"])


def prepare_intake(product: Product, args: object):
    if not isinstance(args, dict) or set(args) != {"answers"}:
        raise FlaminError("Provide only an answers object.")
    answers = args["answers"]
    if not isinstance(answers, dict) or not answers or answers.keys() - INTAKE_FIELDS.keys():
        raise FlaminError("Provide at least one recognized Step 0 intake field.")
    if any(not isinstance(value, str) or not value.strip() for value in answers.values()):
        raise FlaminError("Each proposed answer must be nonblank text.")
    if product.store.checksum_problems():
        raise FlaminError("Workspace integrity check failed. Repair state before recording intake.")

    revision = flow.intake_revision(product.state())
    normalized = [(key, answers[key].strip()) for key in INTAKE_FIELDS if key in answers]
    preview = "\n\n".join(f"{INTAKE_FIELDS[key]}:\n{value}" for key, value in normalized)
    return normalized, revision, (
        f"Review the exact intake answers for {product.root}.\n\n{preview}\n\n"
        "To save these answers, type Save intake and accept. Otherwise decline or cancel."
    )


def complete_intake(product, answers, revision):
    state, changed = flow.record_intake(product, answers, expected_revision=revision)
    return {
        "saved_fields": {key: state["intake"][key] for key in INTAKE_FIELDS if state["intake"].get(key)},
        "changed_fields": changed,
        "missing_fields": phase.missing_intake(state),
    }


def prepare_decision(product, args):
    if (not isinstance(args, dict) or set(args) != {"id", "decision"}
            or not isinstance(args["id"], str) or args["decision"] not in ("approve", "reject")):
        raise FlaminError("Supply a decision ID and choose approve or reject.")
    proposal = review_decision(product, args["id"])
    if not proposal["confirmation_available"]:
        raise FlaminError("This proposal type does not use the confirmation form yet.")
    choice = args["decision"]
    phrase = f"{choice.capitalize()} {proposal['id']}"
    effect = "\n\n" + proposal["effect"] if choice == "approve" and proposal.get("effect") else ""
    message = (
        f"{phrase}: {proposal['title']}\nWorkspace: {proposal['workspace']}\n\n{proposal['payload']}"
        f"{effect}\n\nTo confirm, type {phrase} and accept. Otherwise decline or cancel."
    )
    return phrase, message, partial(complete_decision, product, proposal, choice)


def complete_decision(product, proposal, choice):
    rid = proposal["id"]
    with product.store.transaction():
        if review_decision(product, rid) != proposal:
            raise FlaminError("The proposal changed. Review it again.")
        result = flow.apply_decision(product, rid, choice == "approve", flow.HOST_APPROVER,
                                     assumption_hash=proposal["payload_hash"])
    return {"id": rid, "decision": choice, "result": result}


def success(value):
    return {"structuredContent": value, "content": [{"type": "text", "text": json.dumps(value)}]}


def reply(request_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class IntakeSession:
    def __init__(self, product: Product):
        self.product = product
        self.supports_form = False
        self.pending = None

    def handle(self, request: object) -> dict | None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return rpc_error(None, -32600, "Invalid request")
        method = request.get("method")
        params = request.get("params", {})
        request_id = request.get("id")
        if method is None and "id" in request:
            return self.complete(request)
        if "id" not in request:
            return None
        if not isinstance(params, dict):
            return rpc_error(request_id, -32602, "Expected named parameters")
        if method == "initialize":
            capabilities = params.get("capabilities")
            elicitation = capabilities.get("elicitation") if isinstance(capabilities, dict) else None
            self.supports_form = isinstance(elicitation, dict) and (
                not elicitation or isinstance(elicitation.get("form"), dict)
            )
        if method == "tools/list":
            return reply(request_id, {"tools": [
                *READ_TOOLS, INIT_TOOL, INTAKE_TOOL, STACK_TOOL, DECISION_TOOL, *workflow_tools(),
            ]})
        name = params.get("name")
        if method == "tools/call" and isinstance(name, str) and name in OPERATIONS:
            try:
                result = run_workflow(self.product, name, params.get("arguments", {}))
                return reply(request_id, success(result) if result["ok"] else tool_error(result["message"]))
            except FlaminError as exc:
                return reply(request_id, tool_error(str(exc)))
            except (OSError, ValueError, TypeError, KeyError) as exc:
                return reply(request_id, tool_error(f"Could not complete the workflow operation: {exc}"))
        if method != "tools/call" or name not in (
            INIT_TOOL["name"], INTAKE_TOOL["name"], STACK_TOOL["name"], DECISION_TOOL["name"],
        ):
            return dispatch(self.product, request)
        try:
            args = params.get("arguments")
            if name == STACK_TOOL["name"]:
                return reply(request_id, success(propose_stack(self.product, args)))
            if not self.supports_form:
                raise FlaminError("This host does not provide confirmation forms.")
            if self.pending is not None:
                raise FlaminError("Finish the outstanding confirmation first.")
            if name == DECISION_TOOL["name"]:
                phrase, preview, apply = prepare_decision(self.product, args)
            elif name == INIT_TOOL["name"]:
                if args not in (None, {}):
                    raise FlaminError("Initialize takes no arguments.")
                check_new_workspace(self.product)
                phrase = "Initialize workspace"
                preview = (f"Create the initial FLAMIN state and intake documents in {self.product.root}.\n\n"
                           f"To confirm, type {phrase} and accept.")
                apply = partial(initialize_workspace, self.product)
            else:
                answers, revision, preview = prepare_intake(self.product, args)
                phrase, apply = "Save intake", partial(complete_intake, self.product, answers, revision)
        except FlaminError as exc:
            return reply(request_id, tool_error(str(exc)))
        except (OSError, ValueError, TypeError, KeyError):
            return reply(request_id, tool_error("Workspace state could not be read."))
        confirmation_id = str(uuid4())
        self.pending = (confirmation_id, request_id, phrase, apply)
        return {
            "jsonrpc": "2.0",
            "id": confirmation_id,
            "method": "elicitation/create",
            "params": {
                "message": preview,
                "requestedSchema": {
                    "type": "object",
                    "properties": {"confirmation": {"type": "string", "title": f"Type {phrase}"}},
                    "required": ["confirmation"],
                },
            },
        }

    def complete(self, response: dict) -> dict | None:
        if self.pending is None or response["id"] != self.pending[0]:
            return None
        _, request_id, phrase, apply = self.pending
        self.pending = None
        result = response.get("result")
        if ("error" in response or not isinstance(result, dict) or result.get("action") != "accept"
                or result.get("content") != {"confirmation": phrase}):
            return reply(request_id, tool_error("The operation was not confirmed."))
        try:
            return reply(request_id, success(apply()))
        except FlaminError as exc:
            return reply(request_id, tool_error(str(exc)))
        except (OSError, ValueError, TypeError, KeyError):
            return reply(request_id, tool_error("Could not complete the operation. Check workspace status before retrying."))


def main() -> int:
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_absolute() or not Path(sys.argv[1]).is_dir():
        print("Supply the absolute workspace path.", file=sys.stderr)
        return 2
    session = IntakeSession(Product(Path(sys.argv[1]).resolve(), tool="mcp"))
    for line in sys.stdin.buffer:
        try:
            request = json.loads(line.decode("utf-8"))
        except (UnicodeError, ValueError):
            response = rpc_error(None, -32700, "Parse error")
        else:
            response = session.handle(request)
        if response is not None:
            print(json.dumps(response), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
