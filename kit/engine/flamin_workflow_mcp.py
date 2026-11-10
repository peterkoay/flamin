"""Typed Codex operations for the existing product workflow engine."""

from __future__ import annotations

import re
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO

from flaminlib import cmds, flow
from flaminlib.statefile import STATE_FILES
from flaminlib.util import FlaminError

NAME = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_-]*$"}
TEXT = {"type": "string", "minLength": 1}
FLAG = {"type": "boolean"}
NAMES = {"type": "array", "items": NAME}
RESULT = {"type": "string", "enum": ["pass", "fail"]}
IMPACTS = {"type": "array", "items": {
    "type": "string", "pattern": r"^CR-[0-9]+=[a-z][a-z0-9_]*(,[a-z][a-z0-9_]*)*@(10|[6-9])$",
}}

# Each tool calls a fixed engine operation. Optional fields use the same defaults as the CLI.
OPERATIONS = {
    "flamin_record_analysis": (flow.cmd_analyze, "Record Step 1 requirements.", {"slug": NAME}, {}),
    "flamin_add_module": (flow.cmd_model, "Record a Step 2 module and its dependencies.", {"add_module": NAME},
                          {"depends": (NAMES, None), "profile": (NAME, None)}),
    "flamin_complete_model_step": (flow.cmd_model, "Complete Step 2, 3 or 4.",
                                   {"step": {"type": "integer", "enum": [2, 3, 4]}}, {}),
    "flamin_propose_baseline": (flow.cmd_baseline_review, "Check Step 5 and propose the baseline.", {}, {}),
    "flamin_propose_baseline_amendment": (flow.cmd_baseline_amend, "Propose a baseline amendment.",
                                         {"reason": TEXT, "modules": NAMES}, {}),
    "flamin_close_baseline_amendment": (flow.cmd_model, "Close the current baseline amendment.", {}, {}),
    "flamin_propose_plan": (flow.cmd_plan, "Propose the module's Step 6 interface plan.", {"module": NAME},
                            {"slug": (NAME, None)}),
    "flamin_record_design": (flow.cmd_design, "Lint and record a Step 7 design. Pass the spec filename stem.",
                             {"module": NAME, "spec": NAME}, {}),
    "flamin_generate": (cmds.cmd_generate, "Generate from a recorded Step 7 design. Pass the spec filename stem.",
                        {"module": NAME, "spec": NAME}, {}),
    "flamin_lease_module": (flow.cmd_develop, "Acquire or hand over the Step 8 module lease.", {"module": NAME},
                            {"agent": (NAME, None), "deep": (TEXT, None)}),
    "flamin_complete_development": (flow.cmd_develop, "Record completed Step 8 development.", {"module": NAME}, {}),
    "flamin_release_module_lease": (flow.cmd_develop, "Release the module lease.", {"module": NAME}, {}),
    "flamin_record_wiring": (flow.cmd_wire, "Record Step 9 mock or real wiring and optionally complete the step.",
                             {"module": NAME}, {"mock": (NAMES, None), "real": (NAMES, None), "done": (FLAG, False)}),
    "flamin_record_module_test": (flow.cmd_module_done, "Record the Step 10 self-test result.", {"module": NAME, "result": RESULT},
                                  {"notes": (TEXT, None)}),
    "flamin_record_integration_test": (flow.cmd_integration_test, "Record the Step 11 integration test result.",
                                       {"result": RESULT}, {"reopen": (NAMES, None)}),
    "flamin_review_architecture": (flow.cmd_architecture_review, "Run Step 12 architecture checks.", {}, {}),
    "flamin_propose_release": (flow.cmd_release, "Open the Step 13 release decision.", {}, {}),
    "flamin_raise_change_request": (flow.cmd_cr_new, "Raise a change request.", {"title": TEXT}, {}),
    "flamin_propose_change_list": (flow.cmd_cr_approve, "Propose CR impacts in CR-001=module@step form.",
                                   {"version": {"type": "integer", "minimum": 1}}, {"cr": (IMPACTS, None)}),
}


def check_new_workspace(product) -> None:
    if product.store.exists() or any(product.store.path(name).exists() for name in STATE_FILES):
        raise FlaminError("The workspace already has FLAMIN state. Use continue or choose a new folder.")
    if (product.root / cmds.MAINTENANCE_FLAG).exists():
        raise FlaminError("Choose a product folder outside the master kit in maintenance mode.")
    if not (product.root / "kit" / "stacks").is_dir():
        raise FlaminError("Pair Codex to a product folder containing the FLAMIN kit.")


def initialize_workspace(product) -> dict:
    check_new_workspace(product)
    product.fdir.mkdir(exist_ok=True)
    with product.store.transaction():
        check_new_workspace(product)
        cmds._create_state(product)
        cmds.record_tools(product, ["codex"], replace=True)
        product.log("init", target=".flamin/", reason="Codex workspace initialized")
        product.ledger("init", decisions="adapter codex")
    return {"initialized": True, "next_action": "Start Step 0 intake."}


def workflow_tools() -> list[dict]:
    return [
        {"name": name, "description": description, "inputSchema": {
            "type": "object", "properties": {**required, **{key: spec for key, (spec, _) in optional.items()}},
            "required": list(required), "additionalProperties": False,
        }, "annotations": {"readOnlyHint": False, "destructiveHint": name == "flamin_generate", "openWorldHint": False}}
        for name, (_, description, required, optional) in OPERATIONS.items()
    ]


def _valid(value, schema: dict) -> bool:
    kind = schema["type"]
    if kind == "string":
        return (isinstance(value, str) and bool(value.strip()) and
                ("enum" not in schema or value in schema["enum"]) and
                ("pattern" not in schema or re.fullmatch(schema["pattern"], value) is not None))
    if kind == "integer":
        return (type(value) is int and value >= schema.get("minimum", 0)
                and ("enum" not in schema or value in schema["enum"]))
    if kind == "boolean":
        return type(value) is bool
    return isinstance(value, list) and all(_valid(item, schema["items"]) for item in value)


def run_workflow(product, name: str, supplied) -> dict:
    operation, _, required, optional = OPERATIONS[name]
    if not isinstance(supplied, dict) or required.keys() - supplied.keys() or supplied.keys() - (required.keys() | optional.keys()):
        raise FlaminError("Supply the documented fields for this operation.")
    args = {key: default for key, (_, default) in optional.items()}
    for key, value in supplied.items():
        schema = required[key] if key in required else optional[key][0]
        if not _valid(value, schema):
            raise FlaminError(f"Invalid value for {key}.")
        args[key] = value
    for key in ("depends", "modules", "reopen"):
        if args.get(key) is not None:
            args[key] = ",".join(args[key])
    if operation is flow.cmd_model:
        args = {"add_module": None, "depends": None, "profile": None, "step": None,
                "done": name == "flamin_close_baseline_amendment", **args}
    if operation is flow.cmd_develop:
        args = {"agent": None, "deep": None, "done": name == "flamin_complete_development",
                "release": name == "flamin_release_module_lease", **args}
    if operation in (flow.cmd_design, cmds.cmd_generate):
        design_root = product.fdir / "design"
        spec = design_root / args["module"] / (args["spec"] + ".toml")
        if args["spec"] == "model" or not spec.is_file() or not spec.resolve().is_relative_to(design_root.resolve()):
            raise FlaminError("The design must be a TOML file inside .flamin/design.")
        args["spec"] = product.relpath(spec)
    product.require()
    output = StringIO()
    with product.store.transaction(), redirect_stdout(output):
        if product.store.checksum_problems():
            raise FlaminError("Workspace integrity check failed. Repair state before continuing.")
        code = operation(product, Namespace(**args)) or 0
    message = "\n".join(
        "  Ask the human using flamin_confirm_decision." if line.startswith("  Answer in a terminal:") else line
        for line in output.getvalue().splitlines()
    )
    return {"ok": code == 0, "message": message}
