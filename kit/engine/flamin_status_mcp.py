"""Read-only local MCP pilot. The host binds the process to one workspace."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from flaminlib import approvals, cmds, flow, phase  # noqa: E402
from flaminlib.product import Product, kit_version  # noqa: E402
from flaminlib.profiles import kit_profile, kit_profiles  # noqa: E402
from flaminlib.snapshot import COMMIT_ID, verify_snapshot  # noqa: E402
from flaminlib.util import FlaminError, read_text, sha256_text  # noqa: E402

STATUS_TOOL = {
    "name": "flamin_workspace_status",
    "description": "Read the paired FLAMIN workspace's saved step, eligible roles and pending decisions.",
    "inputSchema": {"type": "object", "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
}
CONTINUE_TOOL = {
    **STATUS_TOOL,
    "name": "flamin_continue",
    "description": "Find the next saved workflow action, recent handoff and formal decisions without advancing state.",
}
REVIEW_TOOL = {
    "name": "flamin_review_decision",
    "description": "Read the exact content and revision of a pending formal proposal. This tool never decides it.",
    "inputSchema": {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
}
VERIFY_TOOL = {
    "name": "flamin_verify_snapshot",
    "description": "Verify the staged tree or a candidate commit against a trusted base commit.",
    "inputSchema": {
        "type": "object",
        "properties": {key: {"type": "string", "pattern": f"^(?:{COMMIT_ID.pattern})$"}
                       for key in ("base", "candidate")},
        "required": ["base"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
}
READ_TOOLS = [STATUS_TOOL, CONTINUE_TOOL, REVIEW_TOOL, VERIFY_TOOL]
FORM_KINDS = {"assumption", "stack", "baseline", "baseline-amend", "plan", "cr-list", "release"}


def _read_state(product: Product) -> tuple[dict, dict]:
    problems = product.store.checksum_problems()
    if problems:
        raise FlaminError("Workspace integrity check failed. " + " ".join(problems))
    return product.state(), approvals.load(product.store)


def _assumptions(product: Product, ledger: dict) -> dict:
    existing = ledger["assumptions"]
    return {
        aid: item for aid, item in approvals.parse_assumptions(product.root).items()
        if (existing.get(aid, {}).get("status", "open") == "open" or
            existing[aid].get("text_hash") != sha256_text(item["text"]))
    }


def workspace_status(product: Product) -> dict:
    if not product.store.exists():
        return {"initialized": False, "kit_version": kit_version()}

    state, ledger = _read_state(product)
    return _workspace_status(state, ledger)


def _workspace_status(state: dict, ledger: dict) -> dict:
    pending = [
        {"id": request["id"], "title": request["title"]}
        for request in ledger["requests"].values()
        if request["status"] in ("pending", "asked")
    ]
    roles = phase.allowed_agents(state)
    if state["phase"] == 0 and state.get("stack_approved"):
        roles = {"business", "analyst"}
    elif _ready_for_integration(state):
        roles = {"tester"}
    return {
        "initialized": True,
        "kit_version": state.get("kit_version"),
        "product": state.get("product"),
        "version": state["version"],
        "phase": state["phase"],
        "step": phase.current_step(state),
        "eligible_roles": sorted(roles),
        "missing_intake": phase.missing_intake(state) if state["phase"] == 0 else [],
        "pending_requests": sorted(pending, key=lambda request: request["id"]),
        "recorded_open_assumptions": approvals.open_assumptions(ledger),
    }


def _ready_for_integration(state: dict) -> bool:
    return (state["phase"] == 2 and phase.version_info(state).get("cr_list_approved")
            and bool(state["modules"]) and all(
                module.get("self_test") == "pass" and 10 in module["steps_done"]
                for module in state["modules"].values()))


def continue_workspace(product: Product) -> dict:
    if not product.store.exists():
        return {**workspace_status(product), "next_action": "Pair and initialize a workspace using the setup service."}
    state, ledger = _read_state(product)
    status = _workspace_status(state, ledger)
    formal = [
        {"id": req["id"], "kind": req["kind"], "title": req["title"]}
        for req in ledger["requests"].values()
        if req["status"] in ("pending", "asked") and req["kind"] != "action"
        and not (req["kind"] == "baseline" and state["baseline"].get("confirmed"))
    ]
    for aid, item in _assumptions(product, ledger).items():
        formal.append({"id": aid, "kind": "assumption", "title": item["title"]})
    decided = [
        {"id": item["id"], "kind": item["kind"], "outcome": item["status"]}
        for item in ledger["requests"].values() if item.get("approver") == flow.HOST_APPROVER
    ]
    decided.extend(
        {"id": aid, "kind": "assumption", "outcome": item["status"]}
        for aid, item in ledger["assumptions"].items()
        if item.get("approver") == flow.HOST_APPROVER
    )
    handoff = cmds.latest_handoff(product)
    valid, problems = cmds.validate_handoff(read_text(handoff) or "") if handoff else (False, [])
    action = status["step"]
    if state["phase"] == 0 and not state.get("stack_approved"):
        if status["missing_intake"]:
            action = "Business records the missing intake answers. Save confirmed values with flamin_save_intake."
        elif any(item["kind"] == "stack" for item in formal):
            action = "Review the pending stack proposal and ask the human to confirm the decision."
        else:
            action = "Architect records a stack recommendation, then open a proposal with flamin_propose_stack."
    elif any(item["kind"] == "release" for item in formal):
        action = "Review the release proposal and ask the human to confirm the decision."
    elif _ready_for_integration(state):
        action = "Run integration tests and record the Step 11 result with flamin_record_integration_test."
    return {
        **status,
        "next_action": action,
        "formal_decisions": sorted(formal, key=lambda req: req["id"]),
        "recorded_human_decisions": sorted(decided, key=lambda req: req["id"]),
        "latest_handoff": {"path": product.relpath(handoff), "valid": valid, "problems": problems} if handoff else None,
        "intake_revision": (flow.intake_revision(state) if state["phase"] == 0 and
                            not state.get("stack_approved") else None),
    }


def review_decision(product: Product, rid: str) -> dict:
    state, ledger = _read_state(product)
    effect = ""
    request_data = {}
    if rid.startswith("A-"):
        item = _assumptions(product, ledger).get(rid)
        if item is None:
            raise FlaminError(f"No open assumption with ID {rid}.")
        kind, title, payload = "assumption", item["title"], item["text"]
    else:
        req = ledger["requests"].get(rid)
        if req is None or req["kind"] == "action" or req["status"] not in ("pending", "asked"):
            raise FlaminError(f"No open formal proposal with ID {rid}.")
        kind, title, payload = req["kind"], req["title"], req["payload"]
        request_data = req.get("data", {})
        effect = "\n".join(req.get("preview", [])[1:2])
        if sha256_text(payload) != req["payload_hash"]:
            raise FlaminError(f"The payload digest for {rid} does not match.")
        if kind == "stack":
            flow.intake_revision(state)
            details = json.loads(payload)
            recommendation = read_text(product.fdir / "decisions" / "stack.md") or ""
            if sha256_text(recommendation) != details["recommendation_hash"]:
                raise FlaminError("The stack recommendation changed. Submit a new proposal.")
            if details["profiles"] != req["data"]["profiles"] or len(details["profiles"]) != 1:
                raise FlaminError("This form accepts one shipped profile at the workspace root.")
            chosen = details["profiles"][0]
            name = chosen["name"]
            if (name not in kit_profiles() or chosen["root"] or chosen["new_profile"] or
                    chosen["source"] != f"kit/stacks/{name}"):
                raise FlaminError("The proposed profile cannot be verified against the installed kit.")
            profile = kit_profile(name)
            if (flow._folder_hash(profile.folder) != chosen["profile_hash"] or
                    profile.data.get("targets", []) != chosen["targets"] or
                    profile.data.get("dependencies", {}) != chosen["dependencies"]):
                raise FlaminError("The proposed profile cannot be verified against the installed kit.")
            payload = json.dumps({**details, "recommendation": recommendation, "intake": state["intake"]},
                                 indent=2, sort_keys=True)
        elif kind == "baseline":
            if state["baseline"].get("confirmed"):
                raise FlaminError("The baseline is already confirmed.")
            details = json.loads(payload)
            modules = product.modules()
            if modules != details["modules"] or flow._model_hashes(product, modules) != details["models"]:
                raise FlaminError("The baseline changed. Submit a new proposal.")
            payload = json.dumps({**details, "data_models": {
                module: read_text(product.fdir / "design" / module / "model.toml") for module in modules
            }}, indent=2, sort_keys=True)
        elif kind == "plan":
            module = flow._need_phase2(state, request_data["module"])
            if 6 in module["steps_done"]:
                raise FlaminError("The module plan is already confirmed.")
            plan = product.fdir / "analysis" / request_data["slug"] / "plan.md"
            if read_text(plan) != json.loads(payload)["plan"]:
                raise FlaminError("The plan changed. Submit a new proposal.")
        elif kind == "cr-list":
            details = json.loads(payload)
            fields = ("id", "title", "modules", "from_step")
            current = [{key: item[key] for key in fields} for item in state["crs"]
                       if item["version"] == details["version"]]
            if (details["version"] != state["version"] or current != details["crs"]
                    or phase.version_info(state).get("cr_list_approved")):
                raise FlaminError("The change list changed. Submit a new proposal.")
        elif kind == "release":
            if request_data["version"] != state["version"] or state["phase3"]["architecture_review"] != "pass":
                raise FlaminError("The release is no longer at the reviewed step.")
    return {"workspace": str(product.root), "id": rid, "kind": kind, "title": title, "payload": payload,
            "payload_hash": sha256_text(payload), "state_revision": sha256_text(json.dumps(state, sort_keys=True)),
            "confirmation_available": kind in FORM_KINDS, "effect": effect, "request_data": request_data}


def rpc_error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def tool_error(message: str) -> dict:
    return {"isError": True, "content": [{"type": "text", "text": message}]}


def read_result(operation, product: Product, *args) -> dict:
    try:
        value = operation(product, *args)
    except FlaminError as exc:
        return tool_error(str(exc))
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        return tool_error("Workspace state could not be read. Check its format and permissions.")
    return {"structuredContent": value, "content": [{"type": "text", "text": json.dumps(value)}]}


def dispatch(product: Product, request: object) -> dict | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return rpc_error(None, -32600, "Invalid request")
    if "id" not in request:
        return None
    request_id = request["id"]
    method = request["method"]
    params = request.get("params", {})
    if not isinstance(params, dict):
        return rpc_error(request_id, -32602, "Expected named parameters")

    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "flamin-status-pilot", "version": "0.1.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": READ_TOOLS}
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments", {})
        if name == STATUS_TOOL["name"] and args == {}:
            result = read_result(workspace_status, product)
        elif name == CONTINUE_TOOL["name"] and args == {}:
            result = read_result(continue_workspace, product)
        elif (name == REVIEW_TOOL["name"] and isinstance(args, dict) and set(args) == {"id"}
              and isinstance(args["id"], str)):
            result = read_result(review_decision, product, args["id"])
        elif (name == VERIFY_TOOL["name"] and isinstance(args, dict) and "base" in args
              and set(args) <= {"base", "candidate"} and all(isinstance(value, str) for value in args.values())):
            result = read_result(lambda paired: verify_snapshot(paired.root, args["base"], args.get("candidate")),
                                 product)
        else:
            return rpc_error(request_id, -32602, "Unknown read operation or invalid arguments")
    else:
        return rpc_error(request_id, -32601, "Method not found")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> int:
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_absolute():
        print("The FLAMIN pilot requires one absolute workspace path.", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        print("The paired FLAMIN workspace does not exist.", file=sys.stderr)
        return 2

    product = Product(root=root, tool="mcp")
    for line in sys.stdin.buffer:
        try:
            request = json.loads(line.decode("utf-8"))
        except (UnicodeError, ValueError):
            response = rpc_error(None, -32700, "Parse error")
        else:
            response = dispatch(product, request)
        if response is not None:
            print(json.dumps(response), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
