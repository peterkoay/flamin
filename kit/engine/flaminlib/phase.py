"""Phase and step order (DESIGN §9, §6.2). Pure functions over state dicts."""
from __future__ import annotations

from .profiles import PathInfo

WORKERS = ("business", "analyst", "architect", "planner", "designer", "developer", "tester")
AGENTS = ("orchestrator",) + WORKERS
MAX_LEASES = 10

MANDATORY = [
    ("name", "Product name"),
    ("purpose", "Purpose"),
    ("users", "Target users and actors"),
    ("journeys", "Core user journeys"),
    ("features", "Must-have features for the first release"),
    ("platforms", "Target platforms (web, desktop, iOS, Android, backend)"),
    ("data_sensitivity", "Data sensitivity (personal data, payment data)"),
    ("success", "Success criteria"),
]
MANDATORY_KEYS = [k for k, _ in MANDATORY]

STEP_AGENTS = {
    6: {"planner"}, 7: {"designer"}, 8: {"developer"}, 9: {"developer"}, 10: {"tester"},
}


def new_state(kit_version: str) -> dict:
    return {
        "product": None,
        "kit_version": kit_version,
        "version": 1,
        "build": 0,
        "phase": 0,
        "intake": {},
        "stack_approved": False,
        "phase1": {"steps_done": []},
        "analysis": {},
        "baseline": {"confirmed": False, "at": None, "amendments": 0, "window": None},
        "modules": {},
        "phase3": {"integration_test": None, "architecture_review": None},
        "versions": {"1": {"status": "open", "cr_list_approved": True, "released": None}},
        "crs": [],
        "open_requests": [],
        "counters": {"request": 0, "cr": 0, "ledger": 0},
    }


def missing_intake(state: dict) -> list[str]:
    intake = state.get("intake", {})
    return [label for key, label in MANDATORY if not str(intake.get(key, "")).strip()]


def version_info(state: dict, n: int | None = None) -> dict:
    n = state["version"] if n is None else n
    return state.setdefault("versions", {}).setdefault(str(n), {"status": "open", "cr_list_approved": False, "released": None})


def version_open_for_work(state: dict) -> bool:
    v = version_info(state)
    return v.get("status") == "open" and bool(v.get("cr_list_approved"))


def next_phase1_step(state: dict) -> int | None:
    done = set(state.get("phase1", {}).get("steps_done", []))
    for s in (1, 2, 3, 4, 5):
        if s not in done:
            return s
    return None


def module_next_step(mod: dict) -> int | None:
    done = set(mod.get("steps_done", []))
    for s in (6, 7, 8, 9, 10):
        if s not in done:
            return s
    return None


def current_step(state: dict) -> str:
    phase = state.get("phase", 0)
    v = version_info(state)
    if v.get("status") == "closed":
        return "version closed; next change request opens the next version"
    if phase == 0:
        miss = missing_intake(state)
        if miss:
            return f"Step 0 intake ({len(miss)} mandatory item(s) missing)"
        return "Step 0 stack choice" if not state.get("stack_approved") else "Step 1 ready"
    if phase == 1:
        s = next_phase1_step(state)
        return f"Step {s}" if s else "Step 5 done"
    if phase == 2:
        if not v.get("cr_list_approved"):
            return f"v{state['version']}: waiting for CR list approval"
        parts = []
        for name, mod in sorted(state.get("modules", {}).items()):
            s = module_next_step(mod)
            parts.append(f"{name}: Step {s}" if s else f"{name}: done ({mod.get('self_test')})")
        return "Phase 2 — " + ", ".join(parts) if parts else "Phase 2 (no modules)"
    if phase == 3:
        p3 = state.get("phase3", {})
        if p3.get("integration_test") != "pass":
            return "Step 11 integration test"
        if p3.get("architecture_review") != "pass":
            return "Step 12 architecture review"
        return "Step 13 release proposal"
    return f"phase {phase}"


def allowed_agents(state: dict) -> set[str]:
    """Worker agents that may be launched now (the launch gate)."""
    allowed: set[str] = set()
    phase = state.get("phase", 0)
    v = version_info(state)
    if state.get("baseline", {}).get("window"):
        allowed.add("architect")
    if v.get("status") == "closed" or not v.get("cr_list_approved"):
        return allowed | {"business", "analyst"}
    if phase == 0:
        allowed.add("business")
        if not missing_intake(state):
            allowed.add("architect")  # Step 0 exception: stack recommendation before the Analyst
        return allowed
    if phase == 1:
        s = next_phase1_step(state)
        if s == 1:
            allowed |= {"business", "analyst"}
        elif s in (2, 3, 4, 5):
            allowed.add("architect")
        return allowed
    if phase == 2:
        for mod in state.get("modules", {}).values():
            s = module_next_step(mod)
            if s:
                allowed |= STEP_AGENTS[s]
            if s == 9:  # Step 9 is wiring after logic; the Developer holds both
                allowed.add("developer")
        return allowed
    if phase == 3:
        p3 = state.get("phase3", {})
        if p3.get("integration_test") != "pass":
            allowed.add("tester")
        elif p3.get("architecture_review") != "pass":
            allowed.add("architect")
        return allowed
    return allowed


def check_path_phase(info: PathInfo, relpath: str, state: dict, mode: str = "write",
                     new_hash: str | None = None) -> tuple[bool, str]:
    """Is a write to this path allowed at the current step? mode 'write' (live hook) or 'commit'.

    new_hash is the sha256 of the new content; at commit time an unchanged, human-confirmed data model passes.
    """
    baseline = state.get("baseline", {})
    confirmed = bool(baseline.get("confirmed"))
    modules = state.get("modules", {})
    window = baseline.get("window") or {}

    if relpath.startswith(".flamin/design/"):
        parts = relpath.split("/")
        if len(parts) < 4:
            return True, "design folder"
        module, fname = parts[2], parts[-1]
        if fname == "model.toml":
            if not confirmed or module in window.get("modules", []):
                return True, "data model before baseline or inside an amend window"
            if new_hash and new_hash == baseline.get("models", {}).get(module):
                return True, "data model as confirmed at the baseline"
            return False, (f"Data model for '{module}' is part of the confirmed baseline. "
                           "Use `flamin baseline-amend \"<reason>\"` (Approval Gate) to change it.")
        if not confirmed:
            return False, "Design specs are Step 7. The baseline (Step 5) is not confirmed yet."
        if module not in modules:
            return False, f"'{module}' is not a module in the confirmed baseline (modules.json)."
        if mode == "commit":
            return True, "design spec for a baseline module"
        if not version_open_for_work(state):
            return False, f"v{state['version']}: the CR list is not approved, so no design or code change may start."
        mod = modules[module]
        if 6 not in mod.get("steps_done", []):
            return False, f"Step 7 needs Step 6 (interface plan) done for '{module}'. Run `flamin plan {module}` first."
        if 10 in mod.get("steps_done", []):
            return False, f"'{module}' finished Step 10. Reopen it through a change request (`flamin cr-new`)."
        return True, "Step 7 design"

    if info.profile is None or not info.is_source:
        return True, "not product code"

    if info.is_test:
        if not confirmed:
            return False, "Tests belong to Phase 2. The baseline (Step 5) is not confirmed yet."
        return True, "test path"

    # Product code.
    if not confirmed:
        return False, ("Product code is Phase 2 (Steps 7 to 10). The baseline (Step 5) is not confirmed yet, "
                       "so no code may be written.")
    if info.module is None:
        if mode == "commit" or (state.get("phase") == 2 and version_open_for_work(state)):
            return True, "shared product code in Phase 2"
        return False, "Product code outside a module may change only in Phase 2 of an open version."
    if info.module not in modules:
        return False, f"'{info.module}' is not a module in the confirmed baseline (modules.json)."
    mod = modules[info.module]
    done = mod.get("steps_done", [])
    if 7 not in done:
        return False, (f"Code for '{info.module}' is written after Step 7 (design and generate). "
                       f"Next step for this module is {module_next_step(mod)}.")
    if mode == "commit":
        return True, "module code after generation"
    if not version_open_for_work(state):
        return False, f"v{state['version']}: the CR list is not approved, so no code change may start."
    if 10 in done:
        return False, f"'{info.module}' finished Step 10. Reopen it through a change request."
    if not mod.get("lease"):
        return False, (f"No agent holds '{info.module}'. The Orchestrator runs `flamin develop {info.module}` "
                       "before delegating (module lease).")
    return True, "Steps 8 to 10 inside a leased module"
