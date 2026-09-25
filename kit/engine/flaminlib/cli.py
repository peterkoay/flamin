"""Command line: `flamin <verb>` (DESIGN §3.3)."""
from __future__ import annotations

import argparse
import io
import sys

from . import cmds, flow, hooks
from .lint import cmd_lint
from .product import Product
from .util import FlaminError


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="flamin", description="flamin engine: state, gates and checks for AI agent teams.")
    sub = ap.add_subparsers(dest="verb", metavar="<verb>")

    def add(name, help_, fn):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("init", "Create .flamin/, render adapters, set up git hooks", cmds.cmd_init)
    sp.add_argument("--tool", choices=["claude", "codex", "cursor", "all"], default=None)
    sp.add_argument("--new-product", action="store_true", help="refuse if a product already lives here")
    add("status", "Show product, version, phase, current step, open requests", cmds.cmd_status)
    sp = add("doctor", "Report blockers on this machine", cmds.cmd_doctor)
    sp.add_argument("--kit", action="store_true", help="also check the master kit is clean")
    sp.add_argument("--clear-stale-lock", action="store_true")
    sp.add_argument("--fix", action="store_true", help="re-render machine-local adapter files")
    sp.add_argument("--tool", choices=["claude", "codex", "cursor", "all"], default=None)
    sp.add_argument("--update-manifest", action="store_true", help="kit maintainers: rewrite kit/MANIFEST")
    add("resume", "Show the last completed step and the next step", cmds.cmd_resume)

    sp = add("intake", "Step 0: record intake answers, report missing items, open the stack gate", flow.cmd_intake)
    sp.add_argument("--set", action="append", metavar="KEY=VALUE")
    sp.add_argument("--check", action="store_true")
    sp.add_argument("--stack", action="append", metavar="PROFILE[:ROOT]")
    sp = add("analyze", "Step 1: record requirements for a slug", flow.cmd_analyze)
    sp.add_argument("slug")
    sp = add("model", "Steps 2-4: modules, data model, aggregates", flow.cmd_model)
    sp.add_argument("--add-module")
    sp.add_argument("--depends")
    sp.add_argument("--profile")
    sp.add_argument("--step", type=int)
    sp.add_argument("--done", action="store_true", help="close a baseline amend window")
    add("baseline-review", "Step 5: checklist and baseline gate", flow.cmd_baseline_review)
    sp = add("baseline-amend", "Logged change to a confirmed baseline (Approval Gate)", flow.cmd_baseline_amend)
    sp.add_argument("reason")
    sp.add_argument("--modules")
    sp = add("plan", "Step 6: interface batches (plan gate)", flow.cmd_plan)
    sp.add_argument("module")
    sp.add_argument("--slug")
    sp = add("design", "Step 7: record a design spec", flow.cmd_design)
    sp.add_argument("module")
    sp.add_argument("spec")
    sp = add("generate", "Step 7: run flamin-generate on a spec", cmds.cmd_generate)
    sp.add_argument("spec")
    sp = add("lint", "Run flamin-lint on specs, portability and drift", cmd_lint)
    sp.add_argument("spec", nargs="*")
    sp.add_argument("--portability", action="store_true")
    sp.add_argument("--drift", action="store_true")
    sp = add("develop", "Step 8: take the module lease and record progress", flow.cmd_develop)
    sp.add_argument("module")
    sp.add_argument("--agent")
    sp.add_argument("--done", action="store_true")
    sp.add_argument("--release", action="store_true")
    sp.add_argument("--deep", metavar="REASON", help="run the Developer on the Deep tier, with the reason")
    sp = add("wire", "Step 9: cross-module wiring and mocks", flow.cmd_wire)
    sp.add_argument("module")
    sp.add_argument("--mock", action="append")
    sp.add_argument("--real", action="append")
    sp.add_argument("--done", action="store_true")
    sp = add("module-done", "Step 10: record self-test results; release the lease", flow.cmd_module_done)
    sp.add_argument("module")
    sp.add_argument("--result", choices=["pass", "fail"], required=True)
    sp.add_argument("--notes")
    sp = add("integration-test", "Step 11: integration test result", flow.cmd_integration_test)
    sp.add_argument("--result", choices=["pass", "fail"], required=True)
    sp.add_argument("--reopen", metavar="MODULES")
    add("architecture-review", "Step 12: whole-product boundary and lock scan", flow.cmd_architecture_review)
    add("release", "Step 13: close the version, build the release proposal, open the release gate", flow.cmd_release)
    sp = add("cr-new", "Raise a change request", flow.cmd_cr_new)
    sp.add_argument("title")
    sp = add("cr-approve", "Approval Gate for a version's CR list", flow.cmd_cr_approve)
    sp.add_argument("version")
    sp.add_argument("--cr", action="append", metavar="CR-001=moduleA,moduleB@STEP")
    sp.add_argument("--yes", action="store_true")
    sp.add_argument("--no", action="store_true")
    sp = add("approve", "Answer an approval request or assumption (human only)", flow.cmd_approve)
    sp.add_argument("id", nargs="?")
    sp.add_argument("--yes", action="store_true")
    sp.add_argument("--no", action="store_true")
    sp = add("audit", "Show the audit log as readable lines", cmds.cmd_audit)
    sp.add_argument("--stats", action="store_true")
    sp.add_argument("--day")
    sp.add_argument("--tail", type=int, default=0)
    sp.add_argument("--json", action="store_true")
    sp = add("upgrade", "Update kit files from a newer master kit (human only)", cmds.cmd_upgrade)
    sp.add_argument("--from", dest="source", required=True)
    sp.add_argument("--migrate", action="store_true")
    sp.add_argument("--yes", action="store_true")
    for name, fn in (("check-lock", cmds.cmd_check_lock), ("check-boundary", cmds.cmd_check_boundary)):
        sp = add(name, f"{name} on a file, optionally against proposed new content", fn)
        sp.add_argument("path")
        sp.add_argument("--new-file")
    sp = add("check-phase", "Phase and step order check; state checksum check", cmds.cmd_check_phase)
    sp.add_argument("path", nargs="?")
    sp.add_argument("--agent")
    sp = add("check-staged", "Backstop checks on the staged diff (or --range base..head in CI)", cmds.cmd_check_staged)
    sp.add_argument("--range")
    sp = add("hook", "Single entry point for all tool hooks", None)
    sp.add_argument("event")
    sp.add_argument("--tool", required=True, choices=["claude", "codex", "cursor"])
    sp = add("handoff", "Write a handoff skeleton, or validate one", cmds.cmd_handoff)
    sp.add_argument("--validate", metavar="FILE")
    sp.add_argument("--stdin", action="store_true", help="write the handoff from stdin (validated first)")
    add("rebuild-locks", "Rebuild locks.json from inline markers", cmds.cmd_rebuild_locks)
    return ap


def main(argv: list[str]) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "verb", None):
        parser.print_help()
        return 0
    if args.verb == "hook":
        return hooks.run(args.event, args.tool)
    args._argv = " ".join(argv)
    p = Product()
    try:
        return args.fn(p, args) or 0
    except FlaminError as exc:
        sys.stderr.write(f"flamin: {exc}\n")
        try:
            if p.store.exists() and args.verb not in ("status", "resume", "audit"):
                p.log(args.verb, target=" ".join(argv[1:])[:200], decision="deny", reason=str(exc)[:500])
        except Exception:  # noqa: BLE001
            pass
        return exc.code
