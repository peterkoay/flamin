"""Opt-in MCP server for Step 0 intake with a host-mediated confirmation form."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

sys.dont_write_bytecode = True

from flamin_status_mcp import READ_TOOLS, dispatch, review_decision, rpc_error, tool_error  # noqa: E402
from flaminlib import flow, phase  # noqa: E402
from flaminlib.product import Product  # noqa: E402
from flaminlib.profiles import kit_profiles  # noqa: E402
from flaminlib.util import FlaminError  # noqa: E402

INTAKE_FIELDS = dict(phase.MANDATORY)
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
CONFIRMATION = {"confirmation": "Save intake"}
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
    return review_decision(product, request["id"], protected=True)


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


def reply(request_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class IntakeSession:
    def __init__(self, product: Product, decision_database: Path | None = None):
        self.product = product
        self.decision_database = decision_database
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
            stack_tools = [STACK_TOOL] if self.decision_database is not None else []
            return reply(request_id, {"tools": [*READ_TOOLS, INTAKE_TOOL, *stack_tools]})
        if method == "tools/call" and params.get("name") == STACK_TOOL["name"] and self.decision_database is not None:
            try:
                proposal = propose_stack(self.product, params.get("arguments"))
            except FlaminError as exc:
                return reply(request_id, tool_error(str(exc)))
            except (OSError, ValueError, TypeError, KeyError):
                return reply(request_id, tool_error("The stack proposal could not be prepared."))
            return reply(request_id, {"structuredContent": proposal,
                                      "content": [{"type": "text", "text": json.dumps(proposal)}]})
        if method != "tools/call" or params.get("name") != INTAKE_TOOL["name"]:
            return dispatch(self.product, request, self.decision_database)
        if not self.supports_form:
            return reply(request_id, tool_error("This host does not provide intake confirmation forms."))
        if self.pending is not None:
            return reply(request_id, tool_error("Finish the outstanding intake confirmation first."))
        try:
            answers, revision, preview = prepare_intake(self.product, params.get("arguments"))
        except FlaminError as exc:
            return reply(request_id, tool_error(str(exc)))
        except (OSError, ValueError, TypeError, KeyError):
            return reply(request_id, tool_error("Workspace state could not be read."))
        confirmation_id = str(uuid4())
        self.pending = (confirmation_id, request_id, answers, revision)
        return {
            "jsonrpc": "2.0",
            "id": confirmation_id,
            "method": "elicitation/create",
            "params": {
                "message": preview,
                "requestedSchema": {
                    "type": "object",
                    "properties": {"confirmation": {"type": "string", "title": "Type Save intake"}},
                    "required": ["confirmation"],
                },
            },
        }

    def complete(self, response: dict) -> dict | None:
        if self.pending is None or response["id"] != self.pending[0]:
            return None
        _, request_id, answers, revision = self.pending
        self.pending = None
        result = response.get("result")
        if ("error" in response or not isinstance(result, dict) or result.get("action") != "accept"
                or result.get("content") != CONFIRMATION):
            return reply(request_id, tool_error("Intake was not confirmed. No answers were saved."))
        try:
            state, changed = flow.record_intake(self.product, answers, expected_revision=revision)
        except FlaminError as exc:
            return reply(request_id, tool_error(str(exc)))
        except (OSError, ValueError, TypeError, KeyError):
            return reply(request_id, tool_error("Could not complete intake. Check workspace status before retrying."))
        saved = {
            "saved_fields": {key: state["intake"][key] for key in INTAKE_FIELDS if state["intake"].get(key)},
            "changed_fields": changed,
            "missing_fields": phase.missing_intake(state),
        }
        return reply(request_id, {"structuredContent": saved, "content": [{"type": "text", "text": json.dumps(saved)}]})


def main() -> int:
    if len(sys.argv) not in (2, 3) or not Path(sys.argv[1]).is_absolute() or not Path(sys.argv[1]).is_dir():
        print("Supply an absolute workspace path and optionally a protected decision database.", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    database = Path(sys.argv[2]) if len(sys.argv) == 3 else None
    if database is not None:
        if not database.is_absolute() or database.resolve().is_relative_to(root):
            print("The decision database must be an absolute path outside the paired workspace.", file=sys.stderr)
            return 2
        database = database.resolve()
    session = IntakeSession(Product(root, tool="mcp"), database)
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
