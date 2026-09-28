"""Flow commands: intake, Phase 1, Phase 2, Phase 3, change requests, approvals (DESIGN §7-§9, §16)."""
from __future__ import annotations

import json
import re
import shutil
import sys

from . import approvals as ap
from . import boundary as bd
from . import locks as lk
from .phase import MANDATORY, MANDATORY_KEYS, MAX_LEASES, WORKERS, missing_intake, module_next_step, version_info
from .product import Product
from .profiles import classify, kit_profile, kit_profiles, load_profile_dir
from .statefile import session_id
from .util import KIT_DIR, FlaminError, iso, read_text, sha256_file, sha256_text, today, write_text


def out(msg: str = "") -> None:
    print(msg)


def preview(understood: str, planned: str, because: str) -> list[str]:
    return [f"1. Understood: {understood}", f"2. Planned:    {planned}", f"3. Because:    {because}"]


def _folder_hash(folder) -> str:
    parts = []
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            parts.append(f"{p.relative_to(folder).as_posix()}:{sha256_file(p)}")
    return sha256_text("\n".join(parts))


def _dump(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True)


# ====================================================================== Step 0

INTAKE_ALIASES = {"product": "name", "product_name": "name", "users_and_actors": "users", "actors": "users",
                  "must_have": "features", "platform": "platforms", "data": "data_sensitivity",
                  "success_criteria": "success"}


def cmd_intake(p: Product, args) -> int:
    if args.stack:
        return _intake_stack(p, args)
    changed = []
    if args.set:
        with p.tx() as (state, _):
            for item in args.set:
                if "=" not in item:
                    raise FlaminError(f"--set needs key=value, got '{item}'")
                key, value = item.split("=", 1)
                key = INTAKE_ALIASES.get(key.strip().lower(), key.strip().lower())
                if state["intake"].get(key) != value.strip():
                    state["intake"][key] = value.strip()
                    changed.append(key)
                if key == "name":
                    state["product"] = value.strip()
        if changed:
            p.log("intake", target="state.intake", reason="recorded " + ", ".join(changed))
            p.ledger("Step 0 intake", decisions="recorded " + ", ".join(changed))
            out("Recorded: " + ", ".join(changed))
        else:
            out("No change needed: intake answers already recorded.")
    state = p.state()
    miss = missing_intake(state)
    if miss:
        out("HALT: mandatory intake items still missing. Step 1 cannot start until each is clear:")
        for m in miss:
            out(f"  - {m}")
        return 2 if args.check or not args.set else 0
    out("Intake complete: all 8 mandatory items are recorded.")
    if not state.get("stack_approved"):
        out("Next: the Architect recommends the stack in .flamin/decisions/stack.md, then "
            "`flamin intake --stack <profile>[:<root>]` opens the stack approval gate.")
    return 0


def _intake_stack(p: Product, args) -> int:
    state = p.state()
    miss = missing_intake(state)
    if miss:
        raise FlaminError("Stack approval refused: intake is not complete. Missing: " + "; ".join(miss))
    rec = p.fdir / "decisions" / "stack.md"
    if not (read_text(rec) or "").strip():
        raise FlaminError("Stack approval refused: the Architect's recommendation is not recorded in "
                          ".flamin/decisions/stack.md (plain-words reasons first).")
    chosen = []
    for item in args.stack:
        name, _, root = item.partition(":")
        root = root.strip().strip("/")
        root = root + "/" if root else ""
        proposed = p.fdir / "decisions" / "proposed-stacks" / name
        if (proposed / "profile.json").exists():
            prof = load_profile_dir(proposed)
            source = f".flamin/decisions/proposed-stacks/{name}"
            new_profile = True
        elif name in kit_profiles():
            prof = kit_profile(name)
            source = f"kit/stacks/{name}"
            new_profile = False
        else:
            raise FlaminError(f"Unknown stack profile '{name}'. Shipped: {', '.join(kit_profiles())}. "
                              "A new profile goes in .flamin/decisions/proposed-stacks/<name>/ (Architect) and "
                              "through this same gate.")
        chosen.append({"name": name, "root": root, "source": source, "new_profile": new_profile,
                       "targets": prof.data.get("targets", []), "dependencies": prof.data.get("dependencies", {}),
                       "profile_hash": _folder_hash(prof.folder)})
    platforms = state["intake"].get("platforms", "").lower()
    warn = []
    if "ios" in platforms:
        warn.append("iOS is a target: iOS builds need macOS with Xcode, or a cloud build service "
                    "(a cloud build service is its own Approval Gate item).")
    payload = _dump({"profiles": chosen, "recommendation_hash": sha256_text(read_text(rec) or ""),
                     "warnings": warn})
    names = ", ".join(c["name"] + (f" at {c['root']}" if c["root"] else "") for c in chosen)
    with p.tx() as (state, appr):
        req = ap.open_request(p.store, state, appr, "stack", f"Stack choice: {names}", payload,
                              preview(f"use stack profile(s) {names} for {state.get('product')}",
                                      "engine writes stack.json and copies the profile(s) into .flamin/stacks/",
                                      "Step 0 stack approval is an Approval Gate item (DESIGN §8.2)"),
                              data={"profiles": chosen})
    p.log("gate-request", target=req["id"], decision="ask", reason=req["title"])
    for w in warn:
        out("NOTE: " + w)
    out(ap.format_request(req))
    return 0


def _apply_stack(p: Product, state, req) -> str:
    profiles = req["data"]["profiles"]
    for c in profiles:
        src = p.root / c["source"]
        if _folder_hash(src) != c["profile_hash"]:
            raise FlaminError(f"Profile {c['name']} changed after the request was made. A new request is needed.")
    stacks = p.fdir / "stacks"
    for c in profiles:
        dst = stacks / c["name"]
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(p.root / c["source"], dst)
    p.store.save("stack", {"profiles": [{"name": c["name"], "root": c["root"]} for c in profiles],
                           "approved": iso(), "request": req["id"]})
    state["stack_approved"] = True
    p.ledger("Step 0 stack approved", decisions=", ".join(c["name"] for c in profiles))
    return "stack.json written; profile(s) copied into .flamin/stacks/"


# ====================================================================== Phase 1

def cmd_analyze(p: Product, args) -> int:
    state = p.state()
    miss = missing_intake(state)
    if miss:
        raise FlaminError("Step 1 refused: intake is not complete (HALT). Missing: " + "; ".join(miss))
    if not state.get("stack_approved"):
        raise FlaminError("Step 1 refused: the stack choice (Step 0) is not approved yet.")
    slug = args.slug
    req = p.fdir / "analysis" / slug / "requirements.md"
    if not (read_text(req) or "").strip():
        raise FlaminError(f"Step 1 refused: .flamin/analysis/{slug}/requirements.md is missing or empty "
                          "(the Analyst writes testable requirements first).")
    ap.require_no_open_assumptions(p.root, p.store, state, slug, "Step 1")
    with p.tx() as (state, _):
        if state["analysis"].get(slug, {}).get("done"):
            out(f"No change needed: requirements for '{slug}' already recorded.")
            return 0
        state["analysis"][slug] = {"done": True, "at": iso(), "hash": sha256_text(read_text(req))}
        if 1 not in state["phase1"]["steps_done"]:
            state["phase1"]["steps_done"].append(1)
        if state["phase"] < 1:
            state["phase"] = 1
    p.log("step", target=f"step1:{slug}", reason="requirements recorded")
    p.ledger("Step 1 requirements", module=slug, files=[p.relpath(req)])
    out(f"Step 1 recorded for '{slug}'. Next: Step 2 module division (`flamin model --add-module <name>`).")
    return 0


def _cycles(modules: dict) -> list[str]:
    graph = {m: set(v.get("depends", [])) for m, v in modules.items()}
    state, path, found = {}, [], []

    def visit(n):
        state[n] = 1
        path.append(n)
        for d in sorted(graph.get(n, ())):
            if state.get(d) == 1:
                found.append(" -> ".join(path[path.index(d):] + [d]))
            elif not state.get(d):
                visit(d)
        path.pop()
        state[n] = 2

    for n in sorted(graph):
        if not state.get(n):
            visit(n)
    return found


def cmd_model(p: Product, args) -> int:
    state = p.state()
    window = state["baseline"].get("window")
    if args.done:
        if not window:
            out("No change needed: no baseline amend window is open.")
            return 0
        mods = p.modules()
        cyc = _cycles(mods)
        if cyc:
            raise FlaminError("Amend window stays open: module dependency cycle(s): " + "; ".join(cyc))
        with p.tx() as (state, _):
            for m in mods:
                state["modules"].setdefault(m, {"steps_done": [], "self_test": None, "lease": None, "mocks": []})
            state["baseline"]["window"] = None
            state["baseline"]["models"] = _model_hashes(p, mods)
        p.log("baseline-amend-close", target=",".join(window.get("modules", [])), reason="Step 5 cycle check passed")
        p.ledger("Baseline amend window closed", module=",".join(window.get("modules", [])))
        out("Amend window closed. Step 5 cycle check passed.")
        return 0
    if state["baseline"].get("confirmed") and not window:
        raise FlaminError("Phase 1 is closed: the baseline is confirmed. Use `flamin baseline-amend \"<reason>\"`.")
    if 1 not in state["phase1"]["steps_done"] and not window:
        raise FlaminError("Steps 2 to 4 need Step 1 (requirements) done first. Run `flamin analyze <slug>`.")
    profiles = p.profiles()
    if args.add_module:
        name = args.add_module
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise FlaminError("Module names use lower case letters, digits and _ (they become folder and package names).")
        if window and name not in window.get("modules", []):
            raise FlaminError(f"The amend window covers only: {', '.join(window['modules'])}")
        prof = next((pr for pr in profiles if pr.name == args.profile), None) if args.profile else (profiles[0] if profiles else None)
        if prof is None:
            raise FlaminError("No approved stack profile to place the module in.")
        if not prof.layered:
            contracts = []
        else:
            contracts = [prof.module_dir(name) + (prof.contracts_dir or "contracts") + "/"]
        deps = [d for d in (args.depends or "").split(",") if d]
        with p.store.transaction():
            mods = p.modules()
            entry = {"contracts": contracts, "depends": deps, "profile": prof.name}
            if mods.get(name) == entry:
                out(f"No change needed: module '{name}' already recorded.")
                return 0
            mods[name] = entry
            cyc = _cycles(mods)
            if cyc:
                raise FlaminError("Refused: this creates a module dependency cycle: " + "; ".join(cyc))
            p.store.save("modules", mods)
        p.log("model", target=f"modules.json:{name}", reason=f"depends on {deps or 'nothing'}")
        p.ledger("Step 2 module", module=name, files=[".flamin/modules.json"], decisions=f"depends: {deps}")
        out(f"Module '{name}' recorded. Contracts: {contracts or 'none (no layers)'}")
        return 0
    if args.step:
        n = args.step
        if n not in (2, 3, 4):
            raise FlaminError("`flamin model --step` takes 2, 3 or 4.")
        done = state["phase1"]["steps_done"]
        if n in done:
            out(f"No change needed: Step {n} already done.")
            return 0
        if n - 1 not in done:
            raise FlaminError(f"Step {n} needs Step {n - 1} done first.")
        mods = p.modules()
        if n == 2:
            if not mods:
                raise FlaminError("Step 2 needs at least one module (`flamin model --add-module <name>`).")
            cyc = _cycles(mods)
            if cyc:
                raise FlaminError("Step 2 refused: dependency cycle(s): " + "; ".join(cyc))
        if n in (3, 4):
            import tomllib
            missing = [m for m in mods if not (p.fdir / "design" / m / "model.toml").exists()]
            if missing:
                raise FlaminError(f"Step {n} needs a data model file .flamin/design/<module>/model.toml for: {', '.join(missing)}")
            if n == 4:
                for m in mods:
                    data = tomllib.loads(read_text(p.fdir / "design" / m / "model.toml"))
                    if not any(e.get("aggregate_root") for e in data.get("entities", [])):
                        raise FlaminError(f"Step 4: module '{m}' has no entity marked aggregate_root = true.")
        with p.tx() as (state, _):
            state["phase1"]["steps_done"].append(n)
        names = {2: "module division", 3: "data model", 4: "aggregates and invariants"}
        p.log("step", target=f"step{n}", reason=names[n])
        p.ledger(f"Step {n} {names[n]}")
        out(f"Step {n} ({names[n]}) recorded.")
        return 0
    raise FlaminError("Use `flamin model --add-module <name> [--depends a,b]`, `--step 2|3|4` or `--done`.")


def _model_hashes(p: Product, mods) -> dict:
    """sha256 of each module's data model as the human confirms it (line endings normalised)."""
    return {m: sha256_text(lk.norm(read_text(p.fdir / "design" / m / "model.toml") or "")) for m in sorted(mods)}


BASELINE_QUESTIONS = [
    "Are module boundaries clear, with no overlapping jobs?",
    "Does the data model cover every agreed journey?",
    "Are aggregate boundaries and transaction scopes right?",
    "Are enums and value objects complete?",
]


def cmd_baseline_review(p: Product, args) -> int:
    state = p.state()
    if state["baseline"].get("confirmed"):
        out("No change needed: the baseline is already confirmed.")
        return 0
    done = state["phase1"]["steps_done"]
    for n in (1, 2, 3, 4):
        if n not in done:
            raise FlaminError(f"Step 5 needs Step {n} done first.")
    mods = p.modules()
    cyc = _cycles(mods)
    checks = [f"[{'pass' if not cyc else 'FAIL'}] Cross-module dependencies free of cycles (engine check)"]
    ap.require_no_open_assumptions(p.root, p.store, state, None, "Step 5 baseline review")
    checks.append("[pass] Every assumption in scope is agreed (engine check)")
    if cyc:
        raise FlaminError("Baseline review failed: " + "; ".join(cyc))
    checks += [f"[human] {q}" for q in BASELINE_QUESTIONS]
    model_hashes = _model_hashes(p, mods)
    payload = _dump({"checklist": checks, "modules": mods, "models": model_hashes})
    with p.tx() as (state, appr):
        req = ap.open_request(p.store, state, appr, "baseline", "Confirm the baseline (Step 5)", payload,
                              preview("confirm modules, data model and aggregates as the baseline",
                                      "engine sets baseline.confirmed = true and phase = 2",
                                      "Step 5 needs a human \"agreed\" (DESIGN §9.1)"))
    out("Step 5 checklist:")
    for c in checks:
        out("  " + c)
    out(ap.format_request(req))
    return 0


def _apply_baseline(p: Product, state, req) -> str:
    data = json.loads(req["payload"])
    mods = p.modules()
    if mods != data["modules"]:
        raise FlaminError("modules.json changed after the baseline request. Run `flamin baseline-review` again.")
    state["baseline"].update({"confirmed": True, "at": iso(), "models": data["models"]})
    if 5 not in state["phase1"]["steps_done"]:
        state["phase1"]["steps_done"].append(5)
    state["phase"] = 2
    for m in mods:
        state["modules"].setdefault(m, {"steps_done": [], "self_test": None, "lease": None, "mocks": []})
    p.ledger("Step 5 baseline confirmed", decisions=f"modules: {', '.join(sorted(mods))}")
    return "baseline confirmed; Phase 2 open"


def cmd_baseline_amend(p: Product, args) -> int:
    state = p.state()
    if not state["baseline"].get("confirmed"):
        raise FlaminError("There is no confirmed baseline to amend yet; Phase 1 is still open.")
    mods = [m for m in (args.modules or "").split(",") if m]
    if not mods:
        raise FlaminError("Name the modules the amend covers: --modules a,b (new module names are allowed).")
    payload = _dump({"reason": args.reason, "modules": sorted(mods)})
    with p.tx() as (state, appr):
        req = ap.open_request(p.store, state, appr, "baseline-amend", f"Baseline amend: {args.reason}", payload,
                              preview(f"change the confirmed baseline for {', '.join(mods)}: {args.reason}",
                                      "log the reason, then open an Architect write window on those data models",
                                      "baseline amend is an Approval Gate item (DESIGN §9.2)"),
                              data={"reason": args.reason, "modules": sorted(mods)})
    out(ap.format_request(req))
    return 0


def _apply_amend(p: Product, state, req, approver) -> str:
    d = req["data"]
    log = p.fdir / "decisions" / "baseline-amendments.md"
    text = read_text(log) or "# Baseline amendments\n\nWritten by the engine before any window opens.\n"
    text += (f"\n## {req['id']} ({iso()})\n- Reason: {d['reason']}\n- Modules: {', '.join(d['modules'])}\n"
             f"- Approver: {approver}\n")
    write_text(log, text)
    state["baseline"]["amendments"] = state["baseline"].get("amendments", 0) + 1
    state["baseline"]["window"] = {"modules": d["modules"], "reason": d["reason"], "request": req["id"]}
    p.ledger("Baseline amend window opened", module=",".join(d["modules"]), decisions=d["reason"])
    return f"amend window open for {', '.join(d['modules'])}; close it with `flamin model --done`"


# ====================================================================== Phase 2

def _need_phase2(state, module: str | None = None, cr_gate: bool = True) -> dict | None:
    if not state["baseline"].get("confirmed"):
        raise FlaminError("Phase 2 refused: the baseline (Step 5) is not confirmed.")
    v = version_info(state)
    if v.get("status") == "closed":
        raise FlaminError(f"v{state['version']} is closed. Raise a change request (`flamin cr-new`) for the next version.")
    if cr_gate and not v.get("cr_list_approved"):
        raise FlaminError(f"v{state['version']}: the CR list is not approved (`flamin cr-approve v{state['version']}`), "
                          "so no design or code change may start.")
    if state.get("phase", 0) != 2:
        raise FlaminError(f"Phase 2 commands need phase 2; the product is in phase {state.get('phase')}.")
    if module is None:
        return None
    mod = state["modules"].get(module)
    if mod is None:
        raise FlaminError(f"'{module}' is not a module in the confirmed baseline.")
    return mod


def cmd_plan(p: Product, args) -> int:
    state = p.state()
    mod = _need_phase2(state, args.module)
    if 6 in mod["steps_done"]:
        out(f"No change needed: Step 6 already done for '{args.module}'.")
        return 0
    slug = args.slug or args.module
    plan = p.fdir / "analysis" / slug / "plan.md"
    text = read_text(plan)
    if not (text or "").strip():
        raise FlaminError(f"Step 6 refused: .flamin/analysis/{slug}/plan.md is missing (the Planner writes it).")
    batches = re.split(r"(?m)^#{2,3}\s+Batch\b.*$", text)[1:]
    sizes = [len(re.findall(r"(?m)^\s*[-*]\s+\S", b)) for b in batches]
    for i, n in enumerate(sizes, 1):
        if n > 5:
            out(f"WARNING: batch {i} has {n} interfaces; keep batches to 5 or fewer (Soft rule).")
    if not batches:
        out("WARNING: no '## Batch' headings found in the plan; batches of 5 or fewer are expected.")
    payload = _dump({"module": args.module, "plan": text})
    with p.tx() as (state, appr):
        req = ap.open_request(p.store, state, appr, "plan", f"Interface plan for {args.module}", payload,
                              preview(f"agree the interface list for '{args.module}' ({sum(sizes)} interfaces, {len(sizes)} batch(es))",
                                      "engine records Step 6 done; Step 7 design may start",
                                      "the plan stays editable until the human agrees (DESIGN §19)"),
                              data={"module": args.module, "slug": slug})
    out(ap.format_request(req))
    return 0


def _apply_plan(p: Product, state, req) -> str:
    m = req["data"]["module"]
    text = read_text(p.fdir / "analysis" / req["data"]["slug"] / "plan.md") or ""
    if json.loads(req["payload"])["plan"] != text:
        raise FlaminError("The plan changed after the request. Run `flamin plan` again.")
    mod = state["modules"][m]
    if 6 not in mod["steps_done"]:
        mod["steps_done"].append(6)
    p.ledger("Step 6 plan agreed", module=m, files=[f".flamin/analysis/{req['data']['slug']}/plan.md"])
    return f"Step 6 done for '{m}'"


def cmd_design(p: Product, args) -> int:
    from .lint import lint_spec

    state = p.state()
    mod = _need_phase2(state, args.module)
    if 6 not in mod["steps_done"]:
        raise FlaminError(f"Step 7 needs Step 6 done for '{args.module}'.")
    ap.require_no_open_assumptions(p.root, p.store, state, args.module, "Step 7 design")
    spec = _spec_path(p, args.module, args.spec)
    problems = lint_spec(p, spec)
    if problems:
        for pr in problems:
            out("  - " + pr)
        raise FlaminError(f"Spec {p.relpath(spec)} failed lint; fix it before recording.")
    rp = p.relpath(spec)
    with p.tx() as (state, _):
        specs = state["modules"][args.module].setdefault("specs", [])
        if rp in specs:
            out(f"No change needed: {rp} already recorded.")
            return 0
        specs.append(rp)
        specs.sort()
    p.log("step", target=rp, reason="Step 7 design spec recorded")
    p.ledger("Step 7 design", module=args.module, files=[rp])
    out(f"Design spec recorded: {rp}. Next: `flamin generate {rp}`.")
    return 0


def _spec_path(p: Product, module: str, spec: str):
    from pathlib import Path

    cand = Path(spec)
    if not cand.is_absolute():
        cand = p.root / spec
    if not cand.exists():
        cand = p.fdir / "design" / module / (spec if spec.endswith(".toml") else spec + ".toml")
    if not cand.exists():
        raise FlaminError(f"Spec not found: {spec}")
    return cand


def cmd_develop(p: Product, args) -> int:
    agent = args.agent or "developer"
    if agent not in WORKERS:
        raise FlaminError(f"Unknown agent '{agent}'.")
    holder = f"{agent}@{session_id()}"
    with p.tx() as (state, _):
        mod = _need_phase2(state, args.module)
        done = mod["steps_done"]
        if 7 not in done:
            raise FlaminError(f"Step 8 needs Step 7 (design and generate) done for '{args.module}'.")
        if args.release:
            if not mod.get("lease"):
                out("No change needed: no lease held.")
                return 0
            mod["lease"] = None
            p.log("lease-release", target=args.module)
            out(f"Lease on '{args.module}' released.")
            return 0
        if args.done:
            if 8 in done:
                out(f"No change needed: Step 8 already done for '{args.module}'.")
                return 0
            if not mod.get("lease"):
                raise FlaminError(f"Step 8 is recorded by the lease holder; no lease on '{args.module}'.")
            done.append(8)
            p.log("step", target=f"step8:{args.module}", reason="business logic in extension points done")
            p.ledger("Step 8 develop", module=args.module)
            out(f"Step 8 done for '{args.module}'. Next: Step 9 (`flamin wire {args.module}`).")
            return 0
        if 10 in done:
            raise FlaminError(f"'{args.module}' finished Step 10. Reopen it through a change request.")
        if mod.get("lease"):
            if mod["lease"].split("@")[0] == agent:
                out(f"No change needed: '{args.module}' is already leased to {mod['lease']}.")
                return 0
            mod["lease"] = holder  # hand over inside the same module (one agent per module at a time)
        else:
            active = sum(1 for m in state["modules"].values() if m.get("lease"))
            if active >= MAX_LEASES:
                raise FlaminError(f"Refused: {MAX_LEASES} modules already have an active agent (parallel cap).")
            mod["lease"] = holder
        if args.deep:
            mod["deep_reason"] = args.deep
    p.log("lease", target=args.module, reason=f"{holder}" + (f"; Deep tier: {args.deep}" if args.deep else ""),
          model_tier="Deep" if args.deep else None)
    p.ledger("Step 8 lease", module=args.module, decisions=holder + (f", Deep: {args.deep}" if args.deep else ""))
    out(f"'{args.module}' leased to {holder}. The agent may now write inside this module.")
    return 0


def cmd_wire(p: Product, args) -> int:
    with p.tx() as (state, _):
        mod = _need_phase2(state, args.module)
        if 8 not in mod["steps_done"]:
            raise FlaminError(f"Step 9 needs Step 8 done for '{args.module}'.")
        changed = False
        for other in (args.mock or []):
            if other not in mod.setdefault("mocks", []):
                mod["mocks"].append(other)
                changed = True
        for other in (args.real or []):
            if other in mod.get("mocks", []):
                mod["mocks"].remove(other)
                changed = True
        if args.done:
            if 9 in mod["steps_done"]:
                out(f"No change needed: Step 9 already done for '{args.module}'.")
            else:
                mod["steps_done"].append(9)
                changed = True
                out(f"Step 9 done for '{args.module}'. Mocks in use: {mod.get('mocks') or 'none'}.")
        if not changed and not args.done:
            out(f"Wiring for '{args.module}': mocks {mod.get('mocks') or 'none'}.")
    p.log("step", target=f"step9:{args.module}", reason=f"mocks {args.mock or []} real {args.real or []} done={args.done}")
    p.ledger("Step 9 wire", module=args.module, decisions=f"mocks {args.mock or []}, real {args.real or []}")
    return 0


def module_scan(p: Product, module: str | None = None) -> list[str]:
    """Lock and boundary scan over product files (whole product when module is None)."""
    profiles = p.profiles()
    locks = p.locks().get("files", {})
    mods = p.modules()
    exts = {e for pr in profiles for e in pr.extensions}
    problems = []
    for f in lk.iter_code_files(p.root, exts):
        rp = p.relpath(f)
        info = classify(rp, profiles)
        if module and info.module != module:
            continue
        text = read_text(f)
        entry = locks.get(rp)
        if entry:
            ok, why = lk.check_lock(rp, None, text, entry)
            if not ok:
                problems.append(why)
        elif lk.level_of(text):
            problems.append(f"{rp}: has a FLAMIN:LOCK marker but is not in locks.json (not generated; run `flamin generate`)")
        for v in bd.check_boundary(rp, text, profiles, mods):
            problems.append(f"{rp}: {v}")
    for rp, entry in locks.items():
        if (not module or f"/{module}/" in rp) and not (p.root / rp).exists():
            problems.append(f"{rp}: locked generated file is missing")
    return problems


def cmd_module_done(p: Product, args) -> int:
    state = p.state()
    mod = _need_phase2(state, args.module)
    if 10 in mod["steps_done"]:
        out(f"No change needed: '{args.module}' already finished Step 10 ({mod.get('self_test')}).")
        return 0
    if 9 not in mod["steps_done"]:
        raise FlaminError(f"Step 10 needs Step 9 done for '{args.module}'.")
    problems = module_scan(p, args.module)
    if problems and args.result == "pass":
        for pr in problems:
            out("  - " + pr)
        raise FlaminError(f"Step 10 refused for '{args.module}': lock or boundary check failed.")
    with p.tx() as (state, _):
        mod = state["modules"][args.module]
        mod["self_test"] = args.result
        if args.result == "pass":
            mod["steps_done"].append(10)
            mod["lease"] = None
    p.log("step", target=f"step10:{args.module}", decision="allow" if args.result == "pass" else "deny",
          reason=f"self-test {args.result}" + (f": {args.notes}" if args.notes else ""))
    p.ledger("Step 10 self-test", module=args.module, decisions=f"{args.result} {args.notes or ''}".strip())
    out(f"Step 10 for '{args.module}': self-test {args.result}." + (" Lease released." if args.result == "pass" else ""))
    return 0


# ====================================================================== Phase 3

def cmd_integration_test(p: Product, args) -> int:
    with p.tx() as (state, _):
        if not state["baseline"].get("confirmed"):
            raise FlaminError("Step 11 refused: no confirmed baseline.")
        not_ready = [m for m, v in state["modules"].items() if v.get("self_test") != "pass" or 10 not in v["steps_done"]]
        if not_ready:
            raise FlaminError("Step 11 needs every module at self_test: pass. Not ready: " + ", ".join(sorted(not_ready)))
        if state["phase3"].get("integration_test") == "pass" and args.result == "pass":
            out("No change needed: integration test already passed.")
            return 0
        state["phase"] = 3
        state["phase3"]["integration_test"] = args.result
        state["phase3"]["architecture_review"] = None
        if args.result == "fail" and args.reopen:
            for m in args.reopen.split(","):
                mod = state["modules"].get(m)
                if mod is None:
                    raise FlaminError(f"Unknown module '{m}'.")
                mod["steps_done"] = [s for s in mod["steps_done"] if s < 8]
                mod["self_test"] = None
            state["phase"] = 2
    p.log("step", target="step11", decision="allow" if args.result == "pass" else "deny",
          reason=f"integration test {args.result}" + (f"; reopened {args.reopen}" if args.reopen else ""))
    p.ledger("Step 11 integration test", decisions=args.result + (f", reopened {args.reopen}" if args.reopen else ""))
    out(f"Step 11 integration test: {args.result}." + (f" Reopened from Step 8: {args.reopen}." if args.reopen else ""))
    return 0


def cmd_architecture_review(p: Product, args) -> int:
    state = p.state()
    if state["phase3"].get("integration_test") != "pass":
        raise FlaminError("Step 12 needs Step 11 (integration test) passed.")
    problems = module_scan(p, None)
    cyc = _cycles(p.modules())
    problems += [f"module dependency cycle: {c}" for c in cyc]
    result = "fail" if problems else "pass"
    with p.tx() as (state, _):
        state["phase3"]["architecture_review"] = result
    for pr in problems:
        out("  - " + pr)
    p.log("step", target="step12", decision="allow" if result == "pass" else "deny",
          reason=f"architecture review {result}: {len(problems)} problem(s)")
    p.ledger("Step 12 architecture review", decisions=f"{result}, {len(problems)} problem(s)")
    out(f"Step 12 architecture review: {result} (whole-product lock, boundary and cycle scan).")
    return 0 if result == "pass" else 1


def _version_md(p: Product, n: int):
    return p.fdir / "versions" / f"v{n}.md"


def cmd_release(p: Product, args) -> int:
    from .lint import drift_problems

    state = p.state()
    n = state["version"]
    v = version_info(state)
    if v.get("released") is not None:
        out(f"No change needed: v{n} release decision is final ({'released' if v['released'] else 'not released'}).")
        return 0
    if state["phase3"].get("architecture_review") != "pass":
        raise FlaminError("Step 13 refused: the architecture review (Step 12) has not passed.")
    with p.tx() as (state, appr):
        pending = [r for r in appr["requests"].values()
                   if r["kind"] == "release" and r["status"] in ("pending", "asked") and r["data"].get("version") == n]
        if pending:
            req = pending[0]
            out(f"No change needed: release candidate build {req['data']['build']} for v{n} is already waiting.")
            out(ap.format_request(req))
            return 0
        state["build"] = state.get("build", 0) + 1  # only here: one release candidate, one build number
        build = state["build"]
        v = version_info(state)
        v["status"] = "closed"
        v["closed"] = iso()
        mods = {m: x.get("self_test") for m, x in sorted(state["modules"].items())}
        open_crs = [c for c in state["crs"] if c["version"] > n]
        risks = drift_problems(p)
        open_a = ap.open_assumptions(appr)
        if open_a:
            risks.append(f"open assumptions: {', '.join(open_a)}")
        orphans = sorted((p.fdir / "tmp" / "orphans").glob("*")) if (p.fdir / "tmp" / "orphans").exists() else []
        if orphans:
            risks.append(f"{len(orphans)} orphaned extension bodies in .flamin/tmp/orphans/")
        recommend = "release" if not risks and all(r == "pass" for r in mods.values()) else "hold"
        proposal = {
            "version": f"v{n}", "machine_version": f"{n}.0.0", "build": build,
            "tests": {"modules": mods, "integration_test": state["phase3"]["integration_test"],
                      "architecture_review": state["phase3"]["architecture_review"]},
            "open_crs": [f"{c['id']} {c['title']} (v{c['version']})" for c in open_crs],
            "known_risks": risks or ["none found by the engine"],
            "recommendation": recommend,
        }
        payload = _dump(proposal)
        req = ap.open_request(p.store, state, appr, "release", f"Release v{n} (build {build})", payload,
                              preview(f"release v{n} as {n}.0.0 build {build}",
                                      "on yes the engine marks v{0} released; on no it stays unreleased in the history".format(n),
                                      "production release is an Approval Gate item; the human decision is final (DESIGN §16.3)"),
                              data={"version": n, "build": build})
    md = _version_md(p, n)
    text = read_text(md) or f"# v{n}\n"
    text += (f"\n## Release proposal (build {build}, {today()})\n\n- Machine version: {n}.0.0 build {build}\n"
             f"- Module self-tests: {', '.join(f'{m} {r}' for m, r in mods.items()) or 'none'}\n"
             f"- Integration test: {proposal['tests']['integration_test']}\n"
             f"- Architecture review: {proposal['tests']['architecture_review']}\n"
             f"- Open CRs (next versions): {', '.join(proposal['open_crs']) or 'none'}\n"
             f"- Known risks: {'; '.join(proposal['known_risks'])}\n"
             f"- Recommendation: **{recommend}**\n- Gate request: {req['id']}\n")
    write_text(md, text)
    p.log("release-proposal", target=f"v{n}", decision="ask", reason=f"build {build}, recommendation {recommend}")
    p.ledger("Step 13 release proposal", decisions=f"v{n} build {build}, {recommend}, {req['id']}")
    out(f"v{n} is closed and a release proposal is ready (recommendation: {recommend}).")
    out(ap.format_request(req))
    return 0


def _apply_release(p: Product, state, req, yes: bool, approver: str) -> str:
    n = req["data"]["version"]
    v = version_info(state, n)
    v["released"] = yes
    v["decided"] = iso()
    v["approver"] = approver
    md = _version_md(p, n)
    text = read_text(md) or f"# v{n}\n"
    text += (f"\n## Release decision\n\n- Released: {'yes' if yes else 'no'}\n- Date: {today()}\n"
             f"- Approver: {approver}\n- Request: {req['id']}\n")
    write_text(md, text)
    p.ledger("Step 13 release decision", decisions=f"v{n} {'released' if yes else 'not released'} ({req['id']})")
    return f"v{n} {'released' if yes else 'not released; it stays in the history'}"


# ====================================================================== change requests

def _open_next_version(state) -> int:
    state["version"] += 1
    n = state["version"]
    v = state["versions"].setdefault(str(n), {})
    v.update({"status": "open", "cr_list_approved": False, "released": None, "opened": iso()})
    state["phase3"] = {"integration_test": None, "architecture_review": None}
    state["phase"] = 2 if state["baseline"].get("confirmed") else state["phase"]
    return n


def cmd_cr_new(p: Product, args) -> int:
    with p.tx() as (state, _):
        n = state["version"]
        v = version_info(state)
        opened = None
        if v.get("status") == "closed":
            if v.get("released") is None:
                raise FlaminError(f"v{n} is waiting for its release decision. Answer the release gate first.")
            opened = _open_next_version(state)
            target = opened
        elif v.get("cr_list_approved"):
            target = n + 1
            state["versions"].setdefault(str(target), {"status": "queued", "cr_list_approved": False, "released": None})
        else:
            target = n
        for c in state["crs"]:
            if c["title"] == args.title and c["version"] == target:
                out(f"No change needed: {c['id']} '{args.title}' already raised for v{target}.")
                return 0
        state["counters"]["cr"] = state["counters"].get("cr", 0) + 1
        cid = f"CR-{state['counters']['cr']:03d}"
        state["crs"].append({"id": cid, "title": args.title, "version": target, "status": "open",
                             "modules": [], "from_step": None, "raised": iso()})
    md = _version_md(p, target)
    text = read_text(md) or f"# v{target}\n\n## Change requests\n"
    text += f"\n### {cid}: {args.title}\n- Raised: {today()}\n- Impact, affected modules, risks, questions: (Business and Analyst fill this in)\n"
    write_text(md, text)
    p.log("cr-new", target=cid, reason=f"{args.title} -> v{target}")
    p.ledger("CR raised", decisions=f"{cid} {args.title} -> v{target}")
    if opened:
        out(f"v{opened} opened.")
    out(f"{cid} '{args.title}' joins v{target}." + (" (The current version's CR list is already approved, so it waits for the next version.)" if target > state["version"] else ""))
    return 0


CR_IMPACT_RX = re.compile(r"^(CR-\d+)=([a-z0-9_,]+)@(\d+)$")


def cmd_cr_approve(p: Product, args) -> int:
    n = int(str(args.version).lstrip("v"))
    impacts = {}
    for item in args.cr or []:
        m = CR_IMPACT_RX.match(item)
        if not m:
            raise FlaminError(f"--cr takes CR-001=moduleA,moduleB@<earliest step 6-10>, got '{item}'")
        impacts[m.group(1)] = {"modules": sorted(m.group(2).split(",")), "from_step": int(m.group(3))}
    with p.tx() as (state, appr):
        if n != state["version"]:
            cur = version_info(state)
            if n == state["version"] + 1 and cur.get("status") == "closed" and cur.get("released") is not None:
                _open_next_version(state)
            else:
                raise FlaminError(f"v{n} is not the version in progress (v{state['version']}).")
        v = version_info(state)
        if v.get("cr_list_approved"):
            out(f"No change needed: the v{n} CR list is already approved.")
            return 0
        crs = [c for c in state["crs"] if c["version"] == n]
        if not crs:
            raise FlaminError(f"v{n} has no change requests yet (`flamin cr-new \"<title>\"`).")
        for c in crs:
            if c["id"] in impacts:
                c["modules"], c["from_step"] = impacts[c["id"]]["modules"], impacts[c["id"]]["from_step"]
            if not c["modules"] or not c["from_step"]:
                raise FlaminError(f"{c['id']}: affected modules and earliest step are needed: --cr {c['id']}=<modules>@<step>")
            if c["from_step"] < 6:
                raise FlaminError(f"{c['id']}: a change to the data model needs `flamin baseline-amend` first; "
                                  "then approve the CR from Step 6.")
            unknown = [m for m in c["modules"] if m not in state["modules"]]
            if unknown:
                raise FlaminError(f"{c['id']}: unknown module(s) {unknown}")
    ap.require_no_open_assumptions(p.root, p.store, state, None, "CR list approval")
    payload = _dump({"version": n, "crs": [{k: c[k] for k in ("id", "title", "modules", "from_step")} for c in crs]})
    with p.tx() as (state, appr):
        for c in state["crs"]:
            if c["id"] in impacts:
                c["modules"], c["from_step"] = impacts[c["id"]]["modules"], impacts[c["id"]]["from_step"]
        req = ap.open_request(p.store, state, appr, "cr-list", f"CR list for v{n}", payload,
                              preview(f"approve {len(crs)} change request(s) for v{n}: "
                                      + ", ".join(f"{c['id']} {c['title']}" for c in crs),
                                      "engine reopens only the affected modules from each CR's earliest step",
                                      "the CR list is approved before any code change (DESIGN §16.2)"),
                              data={"version": n})
    decided = _maybe_answer_inline(p, req, args)
    if not decided:
        out(ap.format_request(req))
    return 0


def _apply_cr_list(p: Product, state, req) -> str:
    data = json.loads(req["payload"])
    n = data["version"]
    if n != state["version"]:
        raise FlaminError(f"The request is for v{n}; the version in progress is v{state['version']}.")
    reopen = {}
    for c in data["crs"]:
        for m in c["modules"]:
            reopen[m] = min(reopen.get(m, 99), c["from_step"])
    for c in state["crs"]:
        if c["version"] == n:
            c["status"] = "approved"
    for m, s in sorted(reopen.items()):
        mod = state["modules"][m]
        mod["steps_done"] = [x for x in mod["steps_done"] if x < s]
        mod["self_test"] = None
        mod["lease"] = None
    version_info(state, n)["cr_list_approved"] = True
    state["phase"] = 2
    state["phase3"] = {"integration_test": None, "architecture_review": None}
    p.ledger("CR list approved", decisions=f"v{n}; reopened " + ", ".join(f"{m} from Step {s}" for m, s in sorted(reopen.items())))
    return f"v{n} CR list approved; reopened " + ", ".join(f"{m} from Step {s}" for m, s in sorted(reopen.items()))


# ====================================================================== approve

def _maybe_answer_inline(p: Product, req, args) -> bool:
    """For commands that carry --yes/--no themselves (human-only through the hook)."""
    if getattr(args, "yes", False) or getattr(args, "no", False):
        decide(p, req["id"], yes=bool(args.yes), argv_key=getattr(args, "_argv", ""))
        return True
    return False


def _approver(p: Product, argv_key: str, explicit: bool) -> str:
    tool = ap.take_ask_token(p.root, argv_key) if argv_key else None
    if tool:
        return f"human via {tool} prompt"
    if explicit:
        return "human via terminal"
    return ""


def cmd_approve(p: Product, args) -> int:
    if not args.id:
        with p.tx() as (state, appr):
            ap.sync_assumptions(p.root, appr)
            ap.sync_open_list(state, appr)
        appr = ap.load(p.store)
        pend = [r for r in appr["requests"].values() if r["status"] in ("pending", "asked")]
        opena = ap.open_assumptions(appr)
        if not pend and not opena:
            out("Nothing is waiting for approval.")
        for r in sorted(pend, key=lambda r: r["id"]):
            out(ap.format_request(r))
            out("")
        for a in opena:
            rec = appr["assumptions"][a]
            out(f"Assumption {a} is open: {rec.get('title')}\n  Agree in a terminal: flamin approve {a} --yes   (or --no)")
        return 0
    yes = True if args.yes else False if args.no else None
    decide(p, args.id, yes=yes, argv_key=getattr(args, "_argv", ""))
    return 0


def decide(p: Product, rid: str, yes: bool | None, argv_key: str) -> None:
    approver = _approver(p, argv_key, explicit=yes is not None)
    if not approver:
        if sys.stdin.isatty():
            try:
                ans = input(f"Approve {rid}? Type 'yes' or 'no': ").strip().lower()
            except EOFError:
                ans = ""
            if ans not in ("yes", "no"):
                raise FlaminError("No answer means no. Nothing was changed.")
            yes, approver = ans == "yes", "human via terminal"
        else:
            raise FlaminError(f"{rid} needs a human answer: run `flamin approve {rid} --yes` (or --no) in a terminal.")
    if yes is None:
        yes = True  # the human confirmed the tool's own prompt for this exact command
    if rid.startswith("A-"):
        _decide_assumption(p, rid, yes, approver)
        return
    result = ""
    with p.tx() as (state, appr):
        req = appr["requests"].get(rid)
        if req is None:
            raise FlaminError(f"Unknown request {rid}.")
        if req["status"] not in ("pending", "asked"):
            out(f"No change needed: {rid} is already {req['status']}.")
            return
        if yes:
            kind = req["kind"]
            if kind == "stack":
                result = _apply_stack(p, state, req)
            elif kind == "baseline":
                result = _apply_baseline(p, state, req)
            elif kind == "baseline-amend":
                result = _apply_amend(p, state, req, approver)
            elif kind == "plan":
                result = _apply_plan(p, state, req)
            elif kind == "cr-list":
                result = _apply_cr_list(p, state, req)
            elif kind == "release":
                result = _apply_release(p, state, req, True, approver)
            elif kind == "prune":
                result = "approved; run the same `flamin doctor --fix --tool ... --prune` again to delete the files"
            elif kind == "migrate":
                result = "state migration approved; run `flamin upgrade --migrate` again to perform it"
            elif kind == "action":
                result = "approved for this exact action; ask the agent to retry it once"
            else:
                result = "approved"
            req["status"] = "approved"
        else:
            if req["kind"] == "release":
                result = _apply_release(p, state, req, False, approver)
            else:
                result = "rejected; nothing was changed"
            req["status"] = "rejected"
        req["answered"] = iso()
        req["approver"] = approver
        ap.sync_open_list(state, appr)
    p.log("gate-answer", target=rid, decision="allow" if yes else "deny",
          reason=f"{req['kind']}: {req['title']} -> {result}", approver=approver)
    out(f"{rid} {'approved' if yes else 'rejected'} by {approver}: {result}.")


def _decide_assumption(p: Product, aid: str, yes: bool, approver: str) -> None:
    with p.tx() as (state, appr):
        ap.sync_assumptions(p.root, appr)
        rec = appr["assumptions"].get(aid)
        if rec is None:
            raise FlaminError(f"Unknown assumption {aid}. It must be written in .flamin/decisions/assumptions.md first.")
        new = "agreed" if yes else "rejected"
        if rec["status"] == new:
            out(f"No change needed: {aid} is already {new}.")
            return
        rec.update({"status": new, "answered": iso(), "approver": approver})
    p.log("assumption", target=aid, decision="allow" if yes else "deny", reason=f"{new}: {rec.get('title')}",
          approver=approver)
    p.ledger("Assumption answered", decisions=f"{aid} {new} by {approver}")
    out(f"{aid} {new} by {approver}.")
