"""Core commands: init, status, doctor, resume, handoff, audit, checks, generate, upgrade."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import approvals as ap
from . import audit
from . import boundary as bd
from . import gitops
from . import hooks
from . import locks as lk
from . import manifest as mf
from . import phase as ph
from .flow import module_scan, out
from .lint import portability_problems
from .product import Product, kit_version
from .profiles import classify, product_profiles, load_profile_dir
from .secrets_scan import find_secrets
from .statefile import STATE_FILES, StateLock, Store, sidecar, stale_lock
from .policy import MAINTENANCE_FLAG, maintenance_mode
from .util import KIT_DIR, FlaminError, iso, read_text, sha256_bytes, sha256_text, today, write_text

STATE_FORMAT = json.loads((KIT_DIR / "engine" / "format.json").read_text(encoding="utf-8"))["state_format"]
GITIGNORE = [".flamin/audit/", ".flamin/cache/", ".flamin/tmp/", ".flamin/.lock", ".flamin-backup-*/",
             ".flamin-kit-maintenance", ".claude/settings.local.json", ".cursor/hooks.json", ".env", ".env.*",
             "*.pem", "*.key", "secrets/"]
DOC_TEMPLATES = {
    "business/overview.md": "# Product overview\n\nPurpose, users, what \"good\" looks like. Written by the Business agent.\n",
    "business/glossary.md": "# Glossary\n",
    "business/actors.md": ("# Actors and permissions\n\nActors are people or systems that use the product. They are not "
                           "code layers: every actor's request passes through the same layers.\n\n"
                           "| Actor | Can | Cannot | Sees |\n|---|---|---|---|\n"),
    "business/journeys.md": "# Core user journeys\n",
    "business/rules.md": "# Business rules and team standards\n",
    "business/states.md": "# States and transitions\n",
    "technical/overview.md": "# Technical overview\n\nStack, runtime, module map. Written by the Architect.\n",
    "technical/capabilities.md": "# Technical capabilities\n",
    "decisions/stack.md": "",
    "decisions/assumptions.md": ("# Questions, answers and assumptions\n\n"
                                 "Written by the Business agent. One block per item. The engine registers every id it sees "
                                 "as open. Only `flamin approve <id> --yes` makes it agreed; the Status line here is a note "
                                 "and is never read as a decision.\n\n"
                                 "<!-- Example:\n## A-001: Seat hold time is 10 minutes\n- Scope: booking\n"
                                 "- Question: How long is a seat held before payment?\n- Answer (exact words): \n"
                                 "- Status: open\n-->\n"),
    "decisions/baseline-amendments.md": "# Baseline amendments\n\nWritten by the engine before any window opens.\n",
    "versions/v1.md": "# v1\n\nBuilt from scratch through the full flow.\n",
}
FLAMIN_DIRS = ["stacks", "business", "analysis", "technical", "design", "decisions", "sessions", "audit", "versions", "tmp"]


def python_command() -> str:
    return "python" if os.name == "nt" else "python3"


def os_name() -> str:
    return "windows" if os.name == "nt" else ("macos" if sys.platform == "darwin" else "linux")


# ====================================================================== init

def cmd_init(p: Product, args) -> int:
    from .render import isolate_tool, render_all, rollback_discovery, snapshot_discovery

    if args.tool == "all":
        raise FlaminError("One product uses one AI tool. Choose --tool claude, codex, or cursor.")

    out(f"flamin init (kit {kit_version()}) on {os_name()}")
    out(f"  1. Kit runtime: Python {platform.python_version()} ({sys.executable}); command for this OS: {python_command()}")
    if sys.version_info < (3, 11):
        raise FlaminError("Python 3.11 or newer is required.")
    # 2. git
    if gitops.is_repo(p.root):
        out("  2. Git repo: present")
    else:
        r = subprocess.run(["git", "init", "-q"], cwd=str(p.root), capture_output=True)
        out("  2. Git repo: created" if r.returncode == 0 else
            "  2. WARNING: no git repo and `git init` failed; the pre-commit backstop is not active.")
    # 3 + 4. state
    if (p.root / MAINTENANCE_FLAG).exists():
        raise FlaminError("Refused: master-kit maintenance mode is on (D-46). A human runs "
                          "`flamin kit-maintenance off` first, or copies the master kit for a new product.")
    created = False
    if p.store.exists():
        st = p.store.load("state")
        out(f"  3. Product state: '{st.get('product') or '(unnamed)'}' already lives here; state left unchanged.")
        if args.new_product:
            raise FlaminError("Refused: this folder already holds a product. One folder never mixes two products. "
                              "Copy the master kit again and rename the copy for a new product.")
    else:
        leftovers = [n for n in STATE_FILES if (p.fdir / f"{n}.json").exists()]
        if leftovers:
            raise FlaminError(f"Refused: leftover state files {leftovers} without state.json. Run `flamin doctor`.")
        _create_state(p)
        created = True
        out("  3. Leftover state: none")
        out("  4. Created .flamin/ at phase 0 (intake), version v1")
    # 5. pre-commit
    msg = install_precommit(p.root)
    out(f"  5. {msg}")
    # 6. Exactly one discoverable adapter. Switching an existing product is a human gate.
    if created:
        chosen, why = (args.tool, f"--tool {args.tool}") if args.tool else detect_tool()
    else:
        current, source = product_tools(p)
        if len(current) != 1 and not args.tool:
            raise FlaminError("Product tool is ambiguous. Run `flamin init --tool claude|codex|cursor` and "
                              "answer the tool-switch gate to choose one tool.")
        chosen, why = (args.tool, f"--tool {args.tool}") if args.tool else (current[0], source)
        if current != [chosen]:
            switch_req = switch_tool(p, current, chosen)
            if switch_req is None:
                return 0
    switch_snapshot = snapshot_discovery(p.root) if not created and current != [chosen] else None
    try:
        moved = isolate_tool(p.root, chosen)
        changed = render_all(p.root, chosen)
        tools = record_tools(p, [chosen], replace=True)
        if switch_snapshot is not None:
            finish_switch(p, switch_req, current, chosen)
    except Exception:
        if switch_snapshot is not None:
            rollback_discovery(p.root, switch_snapshot)
            if p.store.load("state").get("tools") != current:
                record_tools(p, current, replace=True)
            with p.tx() as (_state, appr):
                appr["requests"][switch_req["id"]].pop("consumed", None)
        raise
    if not created and (current != [chosen] or hook_changed(chosen, changed)):
        (p.fdir / "tmp" / f"heartbeat-{chosen}").unlink(missing_ok=True)
    out(f"  6. Adapters for {chosen} ({why}): " + (", ".join(changed) if changed else "already current"))
    if moved:
        out("     Archived inactive adapters: " + ", ".join(moved))
    note = codex_action_note(tools, changed)
    if note:
        out("     " + note)
    # 7. .gitignore
    added = ensure_gitignore(p.root)
    out("  7. .gitignore: " + (f"added {len(added)} entr{'y' if len(added) == 1 else 'ies'}" if added else "already current"))
    if created:
        p.log("init", target=".flamin/", reason=f"new product state; adapters {', '.join(tools)}")
        p.ledger("init", decisions=f"adapters {', '.join(tools)}")
        out("\nReady. The Business agent starts intake: product name, purpose, and who will use it.")
    return 0


def _create_state(p: Product) -> None:
    fd = p.fdir
    for d in FLAMIN_DIRS:
        (fd / d).mkdir(parents=True, exist_ok=True)
    with StateLock(p.root):
        state = ph.new_state(kit_version())
        state["created"] = iso()
        state["state_format"] = STATE_FORMAT
        p.store.save("state", state)
        p.store.save("locks", {"files": {}})
        p.store.save("modules", {})
        p.store.save("stack", {"profiles": []})
        p.store.save("models", json.loads((KIT_DIR / "agents" / "models.json").read_text(encoding="utf-8")))
        p.store.save("approvals", ap.empty())
    for relp, text in DOC_TEMPLATES.items():
        f = fd / relp
        if not f.exists():
            write_text(f, text)


def install_precommit(root: Path) -> str:
    if not (root / ".git").is_dir():
        return "Pre-commit hook: skipped (no .git folder)"
    name = "pre-commit.windows" if os.name == "nt" else "pre-commit.posix"
    text = (KIT_DIR / "githooks" / name).read_text(encoding="utf-8").replace("\r\n", "\n")
    target = root / ".git" / "hooks" / "pre-commit"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and read_text(target) == text:
        return "Pre-commit hook: already current"
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    try:
        os.chmod(target, 0o755)
    except OSError:
        pass
    return f"Pre-commit hook: written for {os_name()} (runs `flamin check-staged`)"


def ensure_gitignore(root: Path) -> list[str]:
    gi = root / ".gitignore"
    text = read_text(gi) or ""
    have = {l.strip() for l in text.splitlines()}
    add = [e for e in GITIGNORE if e not in have]
    if add:
        if text and not text.endswith("\n"):
            text += "\n"
        text += ("# flamin\n" if "# flamin" not in have else "") + "\n".join(add) + "\n"
        with open(gi, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    return add


# ====================================================================== status and resume

def hook_warning(p: Product) -> str | None:
    tools, _ = product_tools(p)
    rendered = [t for t, f in (("claude", ".claude/settings.local.json"), ("codex", ".codex/config.toml"),
                               ("cursor", ".cursor/hooks.json")) if t in tools and (p.root / f).exists()]
    beats = hooks.heartbeats(p.root)
    fresh = [t for t, age in beats.items() if age <= hooks.HEARTBEAT_FRESH_SECONDS and t in tools]
    if fresh:
        return None
    detail = (f"hook files present for: {', '.join(rendered)}" if rendered else "no hook files are set up on this machine")
    return ("!!! flamin hooks are not running in this session. Gates are Soft until this is fixed. "
            f"Run `flamin doctor`. ({detail}; no hook ran in the last {hooks.HEARTBEAT_FRESH_SECONDS} s. "
            "If a human ran this in a plain terminal, this warning is expected.)")


def cmd_status(p: Product, args) -> int:
    warn = hook_warning(p) if p.store.exists() else None
    if not p.store.exists():
        if maintenance_mode(p.root):
            out("Master-kit maintenance mode is ON (D-46). The product flow does not apply; kit files are open to "
                "agents. Kit changes must pass `flamin kit-maintenance test` before they can be committed. "
                "A human ends it with `flamin kit-maintenance off`.")
            return 0
        out("No product here yet. This is a clean flamin kit.")
        out("Say \"start a new project\" (or run `flamin init`) to create one.")
        return 0
    s = p.store.load("state")
    appr = ap.load(p.store)
    if ap.sync_assumptions(p.root, appr):
        p.store.save("approvals", appr)
    v = ph.version_info(s)
    out(f"Product: {s.get('product') or '(not named yet)'}")
    released = {True: "released", False: "not released", None: "open" if v.get("status") == "open" else "waiting for release decision"}[v.get("released")]
    out(f"Version: v{s['version']} ({released}); machine version {s['version']}.0.0 build {s.get('build', 0)}")
    out(f"Phase: {s['phase']}   Current: {ph.current_step(s)}")
    if s["phase"] == 0:
        miss = ph.missing_intake(s)
        if miss:
            out("Mandatory intake items missing (HALT): " + "; ".join(miss))
    leases = {m: x["lease"] for m, x in s.get("modules", {}).items() if x.get("lease")}
    if leases:
        out("Module leases: " + ", ".join(f"{m} -> {l}" for m, l in sorted(leases.items())))
    pend = [r for r in appr["requests"].values() if r["status"] in ("pending", "asked")]
    if pend:
        out("Waiting for approval: " + ", ".join(f"{r['id']} ({r['title']})" for r in sorted(pend, key=lambda r: r["id"])))
    opena = ap.open_assumptions(appr)
    if opena:
        out("Open assumptions (need an explicit \"agreed\"): " + ", ".join(opena))
    crs = [c for c in s.get("crs", []) if c["status"] == "open"]
    if crs:
        out("Open change requests: " + ", ".join(f"{c['id']} {c['title']} (v{c['version']})" for c in crs))
    out("Agents that may start now: " + (", ".join(sorted(ph.allowed_agents(s))) or "none"))
    problems = p.store.checksum_problems()
    for pr in problems:
        out("!!! State tamper check: " + pr)
    if warn:
        out("")
        out(warn)
    return 0


def latest_handoff(p: Product) -> Path | None:
    d = p.fdir / "sessions"
    files = sorted((f for f in d.glob("*.md") if f.name != "ledger.md"), key=lambda f: (f.stat().st_mtime, f.name)) if d.exists() else []
    return files[-1] if files else None


def cmd_resume(p: Product, args) -> int:
    s = p.state()
    out(f"Resuming {s.get('product') or 'the product'} at v{s['version']}, phase {s['phase']}.")
    done1 = s["phase1"]["steps_done"]
    out(f"Last completed Phase 1 step: {max(done1) if done1 else 'none'}")
    for m, x in sorted(s.get("modules", {}).items()):
        last = max(x["steps_done"]) if x["steps_done"] else None
        out(f"  {m}: last completed step {last or 'none'}, next {ph.module_next_step(x) or 'done'}")
    out(f"Next: {ph.current_step(s)}")
    h = latest_handoff(p)
    if h:
        ok, problems = validate_handoff(read_text(h) or "")
        out(f"Latest handoff: {p.relpath(h)}" + ("" if ok else " (INVALID: " + "; ".join(problems) + ")"))
        text = read_text(h) or ""
        m = re.search(r"## State Ledger\n(.*?)(?:\n## |\Z)", text, re.S)
        if m:
            out(m.group(1).rstrip())
    else:
        out("No handoff file yet; rebuilding the State Ledger from the step ledger.")
    led = read_text(p.fdir / "sessions" / "ledger.md") or ""
    tail = [l for l in led.splitlines() if l.startswith("- ")][-8:]
    if tail:
        out("Recent step ledger:")
        for l in tail:
            out("  " + l)
    out("Finished steps are not redone. Say \"continue\" to carry on from the next step.")
    return 0


# ====================================================================== handoff

HANDOFF_SECTIONS = ("Objective", "State Ledger", "Artifact References")


def validate_handoff(text: str) -> tuple[bool, list[str]]:
    problems = []
    for name in HANDOFF_SECTIONS:
        m = re.search(r"(?m)^##\s+" + re.escape(name) + r"\s*$(.*?)(?=^##\s|\Z)", text, re.S)
        if not m:
            problems.append(f"missing section '## {name}'")
        elif not re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S).strip():
            problems.append(f"section '## {name}' is empty")
    return not problems, problems


def cmd_handoff(p: Product, args) -> int:
    if args.validate:
        f = Path(args.validate)
        f = f if f.is_absolute() else p.root / f
        text = read_text(f)
        if text is None:
            raise FlaminError(f"No such handoff file: {args.validate}")
        ok, problems = validate_handoff(text)
        if not ok:
            for pr in problems:
                out("  - " + pr)
            p.log("handoff-validate", target=p.relpath(f), decision="deny", reason="; ".join(problems))
            raise FlaminError(f"Handoff rejected: {p.relpath(f)} needs all three sections with content.")
        p.log("handoff-validate", target=p.relpath(f), reason="three sections present")
        out(f"Handoff valid: {p.relpath(f)}")
        return 0
    s = p.state()
    from .statefile import session_id

    name = f"{today()}-{session_id()}.md"
    f = p.fdir / "sessions" / name
    if args.stdin:
        # The Orchestrator has no file-write tool on Claude Code; it hands the text to the engine.
        text = sys.stdin.read().replace("\r\n", "\n")
        ok, problems = validate_handoff(text)
        if not ok:
            for pr in problems:
                out("  - " + pr)
            raise FlaminError("Handoff rejected: it needs all three sections with content. Nothing was written.")
        write_text(f, text if text.endswith("\n") else text + "\n")
        p.log("handoff", target=p.relpath(f), reason="written and validated")
        p.ledger("handoff", files=[p.relpath(f)])
        out(f"Handoff written and valid: {p.relpath(f)}")
        return 0
    if f.exists():
        out(f"No change needed: {p.relpath(f)} already exists; edit it, then validate.")
        return 0
    led = [l for l in (read_text(p.fdir / "sessions" / "ledger.md") or "").splitlines() if l.startswith("- ")][-5:]
    body = (f"## Objective\n<what the task is and what \"done\" means>\n\n## State Ledger\n"
            f"- Current: {ph.current_step(s)}\n" + "".join(f"- Completed: {l[2:]}\n" for l in led) +
            "- Pending: \n- Decisions: \n- Open questions: \n\n## Artifact References\n- Changed: \n- Must read: \n")
    write_text(f, body)
    out(f"Handoff skeleton written: {p.relpath(f)}. Fill in Objective and the lists, then run "
        f"`flamin handoff --validate {p.relpath(f)}`.")
    return 0


# ====================================================================== audit

def cmd_audit(p: Product, args) -> int:
    p.require()
    entries = audit.read_all(p.root, day=args.day)
    if args.stats:
        st = audit.stats(entries)
        if not st:
            out("No agent steps recorded yet.")
        for agent, v in st.items():
            flag = "" if v["avg_steps"] <= 10 else "   (above the target of 10)"
            out(f"{agent:12} tasks {v['tasks']:3}   avg steps {v['avg_steps']:5}   max {v['max_steps']}{flag}")
        return 0
    if args.json:
        for e in entries[-args.tail:] if args.tail else entries:
            out(json.dumps(e, ensure_ascii=False))
        return 0
    for e in entries[-args.tail:] if args.tail else entries:
        out(audit.readable(e))
    return 0


# ====================================================================== checks

def _new_text(args) -> str | None:
    if getattr(args, "new_file", None):
        return read_text(Path(args.new_file))
    return None


def cmd_rebuild_locks(p: Product, args) -> int:
    exts = {e for pr in p.profiles() for e in pr.extensions}
    with p.store.transaction():
        new = lk.rebuild(p.root, p.locks(), exts)
        p.store.save("locks", new)
    out(f"locks.json rebuilt: {len(new['files'])} locked file(s).")
    return 0


def cmd_check_lock(p: Product, args) -> int:
    rp = p.relpath(args.path)
    cur = read_text(p.root / rp)
    new = _new_text(args)
    entry = p.locks().get("files", {}).get(rp)
    ok, why = lk.check_lock(rp, cur if new is not None else None, new if new is not None else cur, entry)
    out(("allow: " if ok else "deny: ") + why)
    return 0 if ok else 1


def cmd_check_boundary(p: Product, args) -> int:
    rp = p.relpath(args.path)
    new = _new_text(args)
    text = new if new is not None else read_text(p.root / rp)
    v = bd.check_boundary(rp, text, p.profiles(), p.modules())
    for x in v:
        out("deny: " + x)
    if not v:
        out("allow: no boundary problem found")
    return 0 if not v else 1


def cmd_check_phase(p: Product, args) -> int:
    s = p.state()
    problems = list(p.store.checksum_problems())
    if args.agent:
        allowed = ph.allowed_agents(s)
        if args.agent not in allowed:
            problems.append(f"agent '{args.agent}' may not start now (allowed: {', '.join(sorted(allowed)) or 'none'})")
    if args.path:
        rp = p.relpath(args.path)
        ok, why = ph.check_path_phase(classify(rp, p.profiles()), rp, s, mode="write")
        if not ok:
            problems.append(why)
    for pr in problems:
        out("deny: " + pr)
    if not problems:
        out("allow: phase, step order and state checksums are fine")
    return 0 if not problems else 1


def cmd_check_staged(p: Product, args) -> int:
    """Backstop: check-lock, check-boundary and check-phase on the staged diff or a commit range."""
    root = p.root
    rng = args.range
    if not gitops.is_repo(root):
        raise FlaminError("check-staged needs a git repo.")
    if rng:
        base, head = gitops.range_ends(rng)
        new_rev, old_rev = head, base
    else:
        new_rev, old_rev = "", "HEAD" if gitops.has_head(root) else None
    changes = gitops.changes(root, rng)
    if not changes:
        out("check-staged: nothing to check.")
        return 0

    def new_of(path):
        return gitops.blob(root, new_rev, path)

    def old_of(path):
        return gitops.blob(root, old_rev, path) if old_rev else None

    problems: list[str] = []
    paths = {c[1] for c in changes}
    # enforcement layer: kit manifest (Heuristic, D-30)
    kit_changed = [c for c in changes if mf.is_kit_path(c[1]) or c[1] == "kit/MANIFEST"]
    if kit_changed and (root / MAINTENANCE_FLAG).exists():
        problems += maintenance_test_check(root, new_of if "kit/MANIFEST" in paths else None)
    if kit_changed:
        mtext = new_of("kit/MANIFEST") if "kit/MANIFEST" in paths else (read_text(root / "kit" / "MANIFEST") if not rng else new_of("kit/MANIFEST"))
        want = mf.parse(mtext)
        for st, path, _ in kit_changed:
            if path == "kit/MANIFEST":
                continue
            data = new_of(path)
            if st == "D":
                if path in want:
                    problems.append(f"{path}: kit file deleted but still listed in kit/MANIFEST")
                continue
            if data is None:
                continue
            if want.get(path) != mf.content_hash(data.encode("utf-8")):
                problems.append(f"{path}: kit file changed and does not match kit/MANIFEST (enforcement layer). "
                                "Only `flamin upgrade` changes kit files.")
    # state files: checksum sidecars
    state_text = new_of(".flamin/state.json") if ".flamin/state.json" in paths else read_text(root / ".flamin" / "state.json")
    for name in STATE_FILES:
        rp = f".flamin/{name}.json"
        if rp in paths or f"{rp}.sha256" in paths:
            body, side = new_of(rp), new_of(rp + ".sha256")
            if body is not None and (side or "").strip() != sha256_bytes(body.replace("\r\n", "\n").encode("utf-8")):
                problems.append(f"{rp}: checksum mismatch (state edited outside the engine)")
    # product checks
    state = json.loads(state_text) if state_text else None
    profiles = product_profiles(root)
    locks_text = new_of(".flamin/locks.json") if ".flamin/locks.json" in paths else read_text(root / ".flamin" / "locks.json")
    locks = (json.loads(locks_text) if locks_text else {}).get("files", {})
    modules = json.loads(read_text(root / ".flamin" / "modules.json") or "{}")
    for st, path, _ in changes:
        if path.startswith(".flamin/") and path.endswith((".json", ".sha256")):
            continue
        new = None if st == "D" else new_of(path)
        old = old_of(path)
        entry = locks.get(path)
        if entry or lk.level_of(old):
            ok, why = lk.check_lock(path, old, new, entry)
            if not ok:
                problems.append(why)
        if new:
            for v in bd.check_boundary(path, new, profiles, modules):
                problems.append(f"{path}: {v}")
            info = classify(path, profiles)
            spans = find_secrets(new, include_passwords=not info.is_test)
            if spans:
                problems.append(f"{path}: looks like it contains a secret ({spans[0][0]})")
        if state is not None and st != "D":
            info = classify(path, profiles)
            ok, why = ph.check_path_phase(info, path, state, mode="commit",
                                          new_hash=sha256_text(lk.norm(new)) if new is not None else None)
            if not ok:
                problems.append(f"{path}: {why}")
    where = f"range {rng}" if rng else "staged diff"
    for pr in problems:
        out("  - " + pr)
    out(f"flamin check-staged ({where}, {len(changes)} change(s)): {'pass' if not problems else f'FAIL, {len(problems)} problem(s)'}")
    if p.store.exists():
        p.log("check-staged", target=where, decision="allow" if not problems else "deny",
              reason=f"{len(problems)} problem(s)" + (": " + problems[0] if problems else ""))
    return 0 if not problems else 1


# ====================================================================== generate

def cmd_generate(p: Product, args) -> int:
    from . import generate as gen
    from .flow import _need_phase2

    spec = Path(args.spec)
    spec = spec if spec.is_absolute() else p.root / spec
    if not spec.exists():
        raise FlaminError(f"Spec not found: {args.spec}")
    rp = p.relpath(spec)
    parts = rp.split("/")
    if len(parts) < 4 or parts[:2] != [".flamin", "design"]:
        raise FlaminError("Specs live in .flamin/design/<module>/<name>.toml")
    module = parts[2]
    state = p.state()
    mod = _need_phase2(state, module)
    if 6 not in mod["steps_done"]:
        raise FlaminError(f"Generation is Step 7; it needs Step 6 done for '{module}'.")
    if rp not in mod.get("specs", []):
        raise FlaminError(f"Record the spec first: `flamin design {module} {rp}` (runs lint).")
    if 10 in mod["steps_done"]:
        raise FlaminError(f"'{module}' finished Step 10. Reopen it through a change request.")
    entry = p.modules().get(module, {})
    prof = next((x for x in p.profiles() if x.name == entry.get("profile")), None) or (p.profiles() or [None])[0]
    if prof is None:
        raise FlaminError("No approved stack profile.")
    with p.store.transaction():
        res = gen.generate(p.root, p.fdir, prof, module, spec, p.locks())
        p.store.save("locks", res["locks"])
        # Step 7 is done only when every feature spec of the module has been generated.
        specs_all = sorted(p.relpath(s) for s in (p.fdir / "design" / module).glob("*.toml") if s.name != "model.toml")
        generated = {e.get("spec") for e in res["locks"]["files"].values()}
        waiting = [s for s in specs_all if s not in generated]
        with p.tx() as (state, _):
            done = state["modules"][module]["steps_done"]
            if not waiting and 7 not in done:
                done.append(7)
                done.sort()
    for o in res["orphans"]:
        out("ORPHAN: " + o)
    p.log("generate", target=rp, reason=f"{len(res['written'])} file(s) written, {len(res['orphans'])} orphan(s)")
    if res["written"]:
        p.ledger("Step 7 generate", module=module, files=res["written"])
    if res["written"]:
        out(f"Generated from {rp} ({prof.name}):")
        for w in res["written"]:
            out("  " + w)
    else:
        out(f"No change needed: generated files for {rp} are already identical.")
    if waiting:
        out(f"Step 7 continues for '{module}': not generated yet: {', '.join(waiting)}")
    else:
        out(f"Step 7 done for '{module}': every spec is generated. Next: Step 8 (`flamin develop {module}`).")
    return 0


# ====================================================================== doctor

KIT_ROOT_ALLOWED = {"flamin", "flamin.cmd", "kit", "docs", ".git", ".gitignore", ".gitattributes", ".github",
                    ".claude", ".codex", ".cursor", "CLAUDE.md", "AGENTS.md", "README.md"}


def windows_store_stub(cmd: str) -> bool:
    exe = shutil.which(cmd)
    return bool(exe and "WindowsApps" in exe)


def cmd_doctor(p: Product, args) -> int:
    from .render import render_all, stale_renders

    problems, notes = [], []
    root = p.root
    # python
    notes.append(f"Python {platform.python_version()} at {sys.executable}")
    if sys.version_info < (3, 12) and time.gmtime().tm_year >= 2027 and time.gmtime().tm_mon >= 10:
        problems.append("Python 3.11 has passed its end of life (October 2027). Install a current Python.")
    cmd = python_command()
    exe = shutil.which(cmd)
    if not exe:
        problems.append(f"`{cmd}` is not on PATH. The launchers and hooks use `{cmd}` on {os_name()}.")
    elif os.name == "nt" and windows_store_stub(cmd):
        problems.append(f"`{cmd}` on PATH is the Windows Store shortcut, not a real Python. Install Python and turn off "
                        "the app execution alias.")
    else:
        r = subprocess.run([exe, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"], capture_output=True, text=True)
        ver = r.stdout.strip()
        notes.append(f"`{cmd}` works: Python {ver}")
        try:
            if tuple(int(x) for x in ver.split(".")) < (3, 11):
                problems.append(f"`{cmd}` is Python {ver}; flamin needs 3.11 or newer.")
        except ValueError:
            problems.append(f"`{cmd}` did not report a version.")
    if os.name == "nt" and windows_store_stub("python3"):
        notes.append("`python3` here is the Windows Store shortcut (expected on Windows; flamin uses `python`).")
    # git
    if shutil.which("git") is None:
        problems.append("git is not installed; the pre-commit backstop needs it.")
    elif gitops.is_repo(root):
        hp = gitops.git(root, "config", "--get", "core.hooksPath", check=False).strip()
        if hp:
            problems.append(f"git core.hooksPath is set to '{hp}', so .git/hooks/pre-commit does not run.")
        elif p.store.exists() and not (root / ".git" / "hooks" / "pre-commit").exists():
            problems.append("The pre-commit backstop is not installed. Run `flamin init`.")
    # lock
    stale = stale_lock(root)
    if stale:
        problems.append(f"Stale state lock (session {stale.get('session')}, {stale.get('age_seconds')} s old, process gone). "
                        "Clear it with `flamin doctor --clear-stale-lock`.")
        if args.clear_stale_lock:
            os.remove(root / ".flamin" / ".lock")
            notes.append("Stale lock cleared.")
            p.log("clear-stale-lock", target=".flamin/.lock", reason=f"session {stale.get('session')}")
            problems.pop()
    elif args.clear_stale_lock:
        notes.append("No stale lock to clear (the engine never removes a live lock).")
    # adapters: only the tools this product uses
    tools, source = product_tools(p)
    if args.tool and args.tool != (tools[0] if len(tools) == 1 else None):
        problems.append("`flamin doctor --tool` cannot switch this product. Use `flamin init --tool <name>` "
                        "and answer its tool-switch gate.")
    if p.store.exists() and len(tools) != 1:
        problems.append("Product tool is ambiguous. Use `flamin init --tool <name>` and answer the tool-switch gate.")
    notes.append(f"AI tools checked: {', '.join(TOOL_LABELS[t] for t in tools) or 'none'} ({source})")
    if args.prune:
        problems.append("`--prune` is retired. `flamin init --tool <name>` archives inactive adapters after approval.")
    repaired_hook = None
    if p.store.exists() or not args.kit:
        if args.fix and len(tools) == 1 and not (args.tool and args.tool != tools[0]):
            from .render import isolate_tool
            hook_before = read_text(root / HOOK_FILES[tools[0]])
            archived = isolate_tool(root, tools[0])
            if archived:
                notes.append("Archived inactive adapters: " + ", ".join(archived))
            changed = render_all(root, tools[0])
            if hook_before != read_text(root / HOOK_FILES[tools[0]]):
                repaired_hook = tools[0]
            if changed:
                notes.append("Re-rendered: " + ", ".join(changed))
                note = codex_action_note(tools, changed)
                if note:
                    notes.append(note)
        if len(tools) == 1:
            from .render import ALL_TOOLS, TOOL_PATHS
            for inactive in ALL_TOOLS:
                if inactive == tools[0]:
                    continue
                for rel in TOOL_PATHS[inactive]:
                    if (root / rel).exists():
                        problems.append(f"Inactive adapter remains discoverable: {rel}. Run `flamin doctor --fix` "
                                        "to archive it outside tool discovery.")
            if tools[0] == "claude" and (root / "AGENTS.md").exists():
                problems.append("Inactive adapter remains discoverable: AGENTS.md. Run `flamin doctor --fix` "
                                "to archive it outside tool discovery.")
            forbidden = {"codex": ".codex/agents/orchestrator.toml",
                         "cursor": ".cursor/agents/orchestrator.md"}.get(tools[0])
            if forbidden and (root / forbidden).exists():
                problems.append(f"Orchestrator sub-agent remains discoverable: {forbidden}. "
                                "Run `flamin doctor --fix` to archive it.")
        for s in stale_renders(root, tools):
            problems.append(f"Adapter file out of date: {s} (run `flamin init --tool <name>` or `flamin doctor --fix`)")
    probe_beats = {}
    for tool in set(tools) | ({"codex"} if args.probe_codex else set()):
        beat = p.fdir / "tmp" / f"heartbeat-{tool}"
        if beat.exists():
            stat = beat.stat()
            probe_beats[tool] = (beat.read_bytes(), stat.st_atime_ns, stat.st_mtime_ns)
        else:
            probe_beats[tool] = None
    if "claude" in tools:
        claude_checks(root, notes, problems, product=p.store.exists())
    if "codex" in tools or args.probe_codex:
        notes.append("Codex: project hooks run only after you trust them in /hooks (and again after every change). "
                     "If the heartbeat warning shows in Codex, that is the cause.")
        codex_checks(root, notes, problems, live=args.probe_codex)
    else:
        notes.append("Codex: not used by this product; checks skipped.")
    if "cursor" in tools:
        cursor_checks(root, notes, problems, product=p.store.exists())
    else:
        notes.append("Cursor: not used by this product; checks skipped.")
    # Doctor's self-check calls the hooks, but must not claim a live AI session heartbeat.
    for tool, prior in probe_beats.items():
        beat = p.fdir / "tmp" / f"heartbeat-{tool}"
        if prior is None or tool == repaired_hook:
            beat.unlink(missing_ok=True)
        else:
            beat.write_bytes(prior[0])
            os.utime(beat, ns=(prior[1], prior[2]))
    exec_bit_check(root, problems)
    # manifest
    for pr in mf.verify_tree(root):
        problems.append("Kit integrity: " + pr)
    # state
    if p.store.exists():
        for pr in p.store.checksum_problems():
            problems.append("State: " + pr)
    for pr in portability_problems(root, include_state=p.store.exists())[:20]:
        problems.append("Portability: " + pr)
    if args.kit:
        problems += kit_cleanliness(root)
        if (root / MAINTENANCE_FLAG).exists():
            problems.append("Master kit: the kit-maintenance flag is set (D-46). A shipped kit never carries it; "
                            "a human runs `flamin kit-maintenance off`.")
    if args.update_manifest:
        write_text(root / "kit" / "MANIFEST", mf.build(root))
        notes.append("kit/MANIFEST rewritten from the current kit files.")
        problems = [x for x in problems if not x.startswith("Kit integrity")]
    for n in notes:
        out("  ok   " + n)
    for pr in problems:
        out("  FAIL " + pr)
    out(f"flamin doctor{' --kit' if args.kit else ''}: {'no blockers found' if not problems else f'{len(problems)} blocker(s)'}")
    return 0 if not problems else 1


def kit_cleanliness(root: Path) -> list[str]:
    """The master kit holds no product code, state, audit logs, caches or secrets (DESIGN §2.3)."""
    from .policy import is_secret_file

    out_ = []
    if (root / ".flamin").exists():
        out_.append("Master kit: .flamin/ exists (product state does not belong in the master kit)")
    for child in root.iterdir():
        if child.name not in KIT_ROOT_ALLOWED and not child.name.startswith(".flamin-backup"):
            out_.append(f"Master kit: unexpected top-level item '{child.name}' (product code or leftovers)")
        if child.name.startswith(".flamin-backup"):
            out_.append(f"Master kit: backup folder '{child.name}'")
    for p in root.rglob("*"):
        if ".git" in p.parts:
            continue
        rp = p.relative_to(root).as_posix()
        if p.is_dir() and p.name in ("__pycache__", "node_modules", ".pytest_cache", "cache", "tmp"):
            out_.append(f"Master kit: cache folder {rp}")
        elif p.is_file() and (p.suffix in (".pyc", ".jsonl", ".log") or is_secret_file(rp)):
            out_.append(f"Master kit: {rp} (cache, log or secret file)")
        elif p.is_file() and p.name == "settings.local.json" or rp == ".cursor/hooks.json":
            out_.append(f"Master kit: machine-local hook file {rp}")
    return out_


# ====================================================================== upgrade

def cmd_upgrade(p: Product, args) -> int:
    from .render import isolate_tool, render_all

    src = Path(args.source).resolve()
    if not (src / "kit" / "VERSION").exists():
        raise FlaminError(f"{src} is not a flamin master kit (no kit/VERSION).")
    old_v, new_v = kit_version(), (read_text(src / "kit" / "VERSION") or "").strip()
    new_format = json.loads(read_text(src / "kit" / "engine" / "format.json") or '{"state_format": 1}')["state_format"]
    cur_format = p.state().get("state_format", STATE_FORMAT) if p.store.exists() else STATE_FORMAT
    new_files = {f.relative_to(src).as_posix() for f in (src / "kit").rglob("*") if f.is_file() and "__pycache__" not in f.parts}
    new_files |= {n for n in ("flamin", "flamin.cmd") if (src / n).exists()}
    old_files = set(mf.kit_files(p.root)) | ({"kit/MANIFEST"} if (p.root / "kit" / "MANIFEST").exists() else set())
    changed = sorted(f for f in new_files if not (p.root / f).exists() or (p.root / f).read_bytes() != (src / f).read_bytes())
    removed = sorted(old_files - new_files)
    out(ap.format_request({"id": "upgrade", "title": f"kit {old_v} -> {new_v}", "preview": [
        f"1. Understood: upgrade the kit from {old_v} to {new_v}",
        f"2. Planned:    replace {len(changed)} changed and remove {len(removed)} old kit file(s); re-render adapters",
        "3. Because:    `flamin upgrade` replaces only kit/, the launchers and rendered adapters (DESIGN §2.8)"],
        "payload": "\n".join([f"changed {f}" for f in changed] + [f"removed {f}" for f in removed])}).rsplit("\n  Answer", 1)[0])
    if new_format != cur_format:
        if not args.migrate:
            raise FlaminError(f"Upgrade stopped: the new kit uses state format {new_format}, this product uses "
                              f"{cur_format}. A migration is needed: `flamin upgrade --from {args.source} --migrate` "
                              "(backup first, Approval Gate, never automatic).")
        return _migrate(p, src, cur_format, new_format)
    if not args.yes:
        out("Nothing changed. Run again with --yes to apply (human-only).")
        return 0
    tools, _ = product_tools(p)
    if len(tools) != 1:
        raise FlaminError("Upgrade stopped: product tool is ambiguous. Choose one with "
                          "`flamin init --tool <name>` and answer the tool-switch gate first.")
    for f in changed:
        dst = p.root / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / f, dst)
    for f in removed:
        (p.root / f).unlink(missing_ok=True)
    isolate_tool(p.root, tools[0])
    rend = render_all(p.root, tools[0])
    if hook_changed(tools[0], rend):
        (p.fdir / "tmp" / f"heartbeat-{tools[0]}").unlink(missing_ok=True)
    if p.store.exists():
        p.log("upgrade", target="kit/", reason=f"{old_v} -> {new_v}: {len(changed)} changed, {len(removed)} removed",
              approver="human via terminal")
    out(f"Kit upgraded to {new_v}. .flamin/ was not touched.")
    note = codex_action_note(tools, rend)
    if note:
        out(note)
    return 0


MIGRATIONS: dict[tuple[int, int], object] = {}


def _migrate(p: Product, src: Path, cur: int, new: int) -> int:
    fn = MIGRATIONS.get((cur, new))
    if fn is None:
        raise FlaminError(f"No tested migration from state format {cur} to {new} exists in this kit. Nothing changed.")
    payload = f"migrate state format {cur} -> {new} using kit at {src.name}"
    with p.tx() as (state, appr):
        req = ap.approved_unused(appr, "migrate", payload)
        if not req:
            r = ap.open_request(p.store, state, appr, "migrate", f"State migration {cur} -> {new}", payload,
                                [f"1. Understood: migrate .flamin/ from format {cur} to {new}",
                                 "2. Planned:    copy .flamin/ to a backup folder, then run the tested migration",
                                 "3. Because:    state migrations are never automatic (DESIGN §2.8, D-13)"])
            out(ap.format_request(r))
            return 0
        req["consumed"] = iso()
    backup = p.root / f".flamin-backup-{kit_version()}"
    if backup.exists():
        raise FlaminError(f"{backup.name} already exists; move it away first.")
    shutil.copytree(p.fdir, backup)
    fn(p)
    out(f"Migrated. Backup kept in {backup.name}/.")
    return 0


# ====================================================================== Codex checks (D-38, D-40)

CODEX_AGENT_KEYS = ("max_depth", "max_concurrent_threads_per_session")
PROBE_COMMAND = "git push --force origin main"  # destructive by flamin's rules, so a working hook must deny it


def codex_checks(root: Path, notes: list, problems: list, live: bool = False) -> None:
    import tomllib

    codex = shutil.which("codex")
    if not codex:
        notes.append("Codex is not installed here; Codex checks skipped.")
        return
    try:
        cfg = tomllib.loads(read_text(root / ".codex" / "config.toml") or "")
    except tomllib.TOMLDecodeError as exc:
        problems.append(f"Codex: .codex/config.toml is not valid TOML ({exc})")
        return
    agents = cfg.get("agents", {})
    # D-38: does this Codex accept the [agents] keys? --strict-config fails fast on an unknown key. A local (--oss)
    # provider that is not running stops the run right after the config loads, so no model is called.
    for key in CODEX_AGENT_KEYS:
        if key not in agents:
            problems.append(f"Codex: [agents] {key} is missing from .codex/config.toml. Run `flamin init --tool codex`.")
            continue
        r = subprocess.run([codex, "exec", "--strict-config", "-c", f"agents.{key}={agents[key]}", "--skip-git-repo-check",
                            "-s", "read-only", "--oss", "--local-provider", "ollama", "flamin config check"],
                           cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=120)
        text = r.stdout + r.stderr
        if "Error loading config" in text or "unknown configuration field" in text:
            last = text.strip().splitlines()[-1][:160] if text.strip() else ""
            problems.append(f"Codex: this Codex rejects [agents] {key} ({last}). "
                            "The engine launch gate still enforces depth 1 (D-38).")
        else:
            notes.append(f"Codex accepts [agents] {key} = {agents[key]}")
    # D-40: does the rendered hook command run, in the shell Codex uses on this OS, and deny?
    hooks = cfg.get("hooks", {}).get("PreToolUse", [])
    try:
        handler = hooks[0]["hooks"][0]
    except (IndexError, KeyError, TypeError):
        problems.append("Codex: no PreToolUse hook in .codex/config.toml. Run `flamin init --tool codex`.")
        return
    payload = json.dumps({"hook_event_name": "PreToolUse", "session_id": "flamin-doctor", "permission_mode": "default",
                          "tool_name": "Bash", "tool_input": {"command": PROBE_COMMAND}})
    if os.name == "nt":
        shell_cmd = ["powershell.exe", "-NoProfile", "-Command", handler.get("command_windows", "")]
    else:
        shell_cmd = ["sh", "-c", handler.get("command", "")]
    r = subprocess.run(shell_cmd, cwd=str(root), input=payload, capture_output=True, text=True, timeout=60)
    try:
        reply = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
        decision = reply.get("hookSpecificOutput", {}).get("permissionDecision")
    except ValueError:
        decision = None
    if r.returncode == 0 and decision == "deny":
        notes.append("Codex: the rendered hook command runs in this OS's hook shell and denies a destructive command "
                     "(JSON deny, exit 0).")
    else:
        problems.append(f"Codex: the rendered hook command did not deny a destructive command when run the way Codex "
                        f"runs it (exit {r.returncode}, decision {decision}). {r.stderr.strip()[:200]}")
    if live:
        codex_live_probe(root, codex, notes, problems)
    else:
        notes.append("Codex: `flamin doctor --probe-codex` proves the hooks inside the installed Codex (one model call).")


def codex_live_probe(root: Path, codex: str, notes: list, problems: list) -> None:
    """Run one harmless Codex command and check the flamin hook heartbeat moved (D-40). Uses the real trust state."""
    beat = root / ".flamin" / "tmp" / "heartbeat-codex"
    if not (root / ".flamin").exists():
        notes.append("Codex live probe skipped: no product state here (the heartbeat lives in .flamin/).")
        return
    before = beat.stat().st_mtime if beat.exists() else 0
    r = subprocess.run([codex, "exec", "--skip-git-repo-check", "-s", "read-only", "-C", str(root),
                        "Run exactly this shell command and nothing else: git status --short"],
                       cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=600)
    after = beat.stat().st_mtime if beat.exists() else 0
    version = subprocess.run([codex, "--version"], capture_output=True, text=True).stdout.strip()
    if after > before:
        notes.append(f"Codex live probe: flamin hooks ran inside {version}.")
    else:
        problems.append(f"Codex live probe: no flamin hook ran inside {version} (exit {r.returncode}). Trust the hooks "
                        "in /hooks and trust the project, then run `flamin doctor --probe-codex` again.")


# ====================================================================== executable bits (D-45)

EXEC_FILES = ("flamin", "kit/githooks/pre-commit.posix", "kit/githooks/pre-commit.windows")


def exec_bit_check(root: Path, problems: list) -> None:
    if not gitops.is_repo(root):
        return
    out_ = gitops.git(root, "ls-files", "-s", *EXEC_FILES, check=False)
    modes = {line.split("\t")[-1]: line.split()[0] for line in out_.splitlines() if "\t" in line}
    for f in EXEC_FILES:
        if f in modes and modes[f] != "100755":
            problems.append(f"{f} is committed without the executable bit (mode {modes[f]}). "
                            f"Run `git update-index --chmod=+x {f}` (D-45).")


# ====================================================================== master-kit maintenance mode (D-46)

def _flag(root: Path) -> dict:
    try:
        return json.loads((root / MAINTENANCE_FLAG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cmd_kit_maintenance(p: Product, args) -> int:
    root = p.root
    flag = root / MAINTENANCE_FLAG
    if args.action == "status":
        out("Master-kit maintenance mode: " + ("ON" if maintenance_mode(root) else "off"))
        return 0
    if args.action == "on":
        if (root / ".flamin").exists():
            raise FlaminError("Refused: this folder holds a product. Maintenance mode is for the master kit only (D-46).")
        if flag.exists():
            out("No change needed: master-kit maintenance mode is already on.")
            return 0
        write_text(flag, json.dumps({"on": iso(), "tested_manifest": None}) + "\n")
        ensure_gitignore(root)
        out("Master-kit maintenance mode is ON. Kit files are open to agents; `flamin init` is refused. "
            "Before committing kit changes run `flamin kit-maintenance test`. End with `flamin kit-maintenance off`.")
        return 0
    if args.action == "off":
        if not flag.exists():
            out("No change needed: master-kit maintenance mode is already off.")
            return 0
        flag.unlink()
        out("Master-kit maintenance mode is off.")
        return 0
    if not flag.exists():
        raise FlaminError("`flamin kit-maintenance test` runs only in maintenance mode (a human runs "
                          "`flamin kit-maintenance on`).")
    tests = KIT_DIR / "engine" / "tests"
    r = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(tests), "-t", str(tests)],
                       cwd=str(root), capture_output=True, text=True)
    report = (r.stdout + r.stderr).strip().splitlines()
    for line in (report if r.returncode != 0 else report[-3:]):
        out("  " + line)
    if r.returncode != 0:
        raise FlaminError("Engine tests failed; kit/MANIFEST was not refreshed. Fix the kit and run the tests again.")
    text = mf.build(root)
    write_text(root / "kit" / "MANIFEST", text)
    data = _flag(root)
    data["tested_manifest"] = sha256_text(text)
    data["tested_at"] = iso()
    write_text(flag, json.dumps(data) + "\n")
    out("Engine tests passed. kit/MANIFEST refreshed and recorded as tested.")
    return 0


def maintenance_test_check(root: Path, new_of) -> list[str]:
    """In maintenance mode, staged kit changes must match the last tested manifest (D-46)."""
    want = _flag(root).get("tested_manifest")
    manifest = new_of("kit/MANIFEST") if new_of else read_text(root / "kit" / "MANIFEST")
    if not want or sha256_text((manifest or "").replace("\r\n", "\n")) != want:
        return ["Kit changes in maintenance mode must pass the engine tests first: run `flamin kit-maintenance test`, "
                "then stage kit/MANIFEST (D-46)."]
    return []


# ====================================================================== product tools

TOOL_LABELS = {"claude": "Claude Code", "codex": "Codex", "cursor": "Cursor"}
HOOK_FILES = {"claude": ".claude/settings.local.json", "codex": ".codex/config.toml",
              "cursor": ".cursor/hooks.json"}


def hook_changed(tool: str, changed: list[str]) -> bool:
    return any(rel.startswith(HOOK_FILES[tool]) for rel in changed)


def detect_tool() -> tuple[str, str]:
    """The AI tool this command runs inside. Only Claude Code sets a documented marker in the agent's own shell
    (CLAUDECODE=1, and CLAUDE_PROJECT_DIR for hooks). No Codex or Cursor marker is verified in their docs, so
    neither is detected; the default is Claude Code."""
    if os.environ.get("CLAUDECODE") == "1" or os.environ.get("CLAUDE_PROJECT_DIR"):
        return "claude", "detected: Claude Code"
    return "claude", "default: no AI tool detected"


def inferred_tools(root: Path) -> list[str]:
    """Legacy products without state['tools']: the tools whose adapter files exist."""
    found = []
    if (root / ".claude" / "settings.json").exists() or (root / "CLAUDE.md").exists():
        found.append("claude")
    if (root / ".codex" / "config.toml").exists():
        found.append("codex")
    if (root / ".cursor" / "agents").is_dir() or (root / ".cursor" / "hooks.json").exists():
        found.append("cursor")
    return found


def product_tools(p: Product, override: str | None = None) -> tuple[list[str], str]:
    """Tools this product uses: --tool, else state['tools'], else inferred from adapter files."""
    from .render import tool_tuple

    if override:
        return list(tool_tuple(override)), f"--tool {override}"
    if p.store.exists():
        tools = p.store.load("state").get("tools")
        if tools:
            return list(tool_tuple(tools)), "product state"
    return inferred_tools(p.root), "adapter files present"


def record_tools(p: Product, tools: list[str], replace: bool = False) -> list[str]:
    """Write state['tools'] (engine only). Adds by default; `replace` is used after an approved prune."""
    from .render import tool_tuple

    with p.tx() as (state, _):
        current = [] if replace else state.get("tools") or []
        state["tools"] = list(tool_tuple(set(current) | set(tools)))
        return state["tools"]


def switch_tool(p: Product, current: list[str], target: str) -> dict | None:
    """The human approves the exact current-to-target transition before any adapter moves."""
    payload = f"switch active AI tool from {','.join(current) or '(unset)'} to {target}"
    with p.tx() as (state, appr):
        req = ap.approved_unused(appr, "tool-switch", payload)
        if not req:
            req = ap.open_request(p.store, state, appr, "tool-switch",
                                  f"Switch this product to {TOOL_LABELS[target]}", payload,
                                  [f"1. Understood: use {TOOL_LABELS[target]} for this product",
                                   "2. Planned:    archive inactive adapters, restore and render the selected adapter",
                                   "3. Because:    switching tools changes the active agent and hook configuration"],
                                  data={"from": current, "to": target})
            out(ap.format_request(req))
            return None
    return req


def finish_switch(p: Product, req: dict, current: list[str], target: str) -> None:
    with p.tx() as (_state, appr):
        appr["requests"][req["id"]]["consumed"] = iso()
    p.log("tool-switch", target=target, reason=f"from {current}", approver=req.get("approver"), request=req["id"])


def codex_action_note(tools, changed) -> str | None:
    if "codex" in tools and any(c.startswith(".codex/config.toml") for c in changed):
        return "ACTION: Codex skips changed hooks silently. Open Codex, run /hooks and trust the flamin hooks again."
    return None


# ====================================================================== Claude Code checks

def claude_checks(root: Path, notes: list, problems: list, product: bool) -> None:
    from .render import claude_local, claude_shared

    shared, local = claude_shared(), claude_local()
    for rel, want in ((".claude/settings.json", shared[".claude/settings.json"]),
                      (".claude/settings.local.json", local[".claude/settings.local.json"])):
        have = read_text(root / rel)
        if have is None:
            if rel.endswith("local.json") and not product:
                notes.append(f"Claude Code: {rel} is machine-local and not rendered here; `flamin init` renders it.")
            else:
                problems.append(f"Claude Code: {rel} is missing. Run `flamin doctor --fix --tool claude`.")
        elif have.replace("\r\n", "\n") != want:
            problems.append(f"Claude Code: {rel} differs from the rendered adapter. Run `flamin doctor --fix --tool claude`.")
        else:
            notes.append(f"Claude Code: {rel} is current.")
    # Heartbeat age first: the probe below runs the hook and would reset it.
    beat = root / ".flamin" / "tmp" / "heartbeat-claude"
    if beat.exists():
        notes.append(f"Claude Code: last hook call {int(time.time() - beat.stat().st_mtime)} s ago (heartbeat-claude).")
    elif product:
        notes.append("Claude Code: no hook has run yet in this product (no heartbeat-claude).")
    # Run the PreToolUse handler exactly as Claude Code would: exec form, placeholders substituted, cwd = root.
    settings = json.loads(read_text(root / ".claude" / "settings.local.json") or local[".claude/settings.local.json"])
    try:
        handler = settings["hooks"]["PreToolUse"][0]["hooks"][0]
    except (KeyError, IndexError, TypeError):
        problems.append("Claude Code: no PreToolUse hook in .claude/settings.local.json. Run `flamin doctor --fix --tool claude`.")
        return
    proj = str(root)
    argv = [handler.get("command", "")] + [a.replace("${CLAUDE_PROJECT_DIR}", proj) for a in handler.get("args", [])]
    payload = json.dumps({"hook_event_name": "PreToolUse", "session_id": "flamin-doctor", "permission_mode": "default",
                          "tool_name": "Bash", "tool_input": {"command": PROBE_COMMAND}})
    try:
        r = subprocess.run(argv, cwd=proj, input=payload, capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, CLAUDE_PROJECT_DIR=proj))
    except (OSError, subprocess.TimeoutExpired) as exc:
        problems.append(f"Claude Code: the PreToolUse hook command did not start ({exc}). Check `{handler.get('command')}`.")
        return
    try:
        decision = json.loads(r.stdout.strip().splitlines()[-1]).get("hookSpecificOutput", {}).get("permissionDecision")
    except (ValueError, IndexError):
        decision = None
    # Claude Code's deny contract (hooks.py, DESIGN §4.1, D-40): JSON deny plus exit code 2, reason on stderr.
    if r.returncode == 2 and decision == "deny":
        notes.append("Claude Code: the PreToolUse hook runs as Claude Code runs it and denies a destructive command "
                     "(JSON deny, exit 2).")
    else:
        problems.append(f"Claude Code: the PreToolUse hook did not deny a destructive command (exit {r.returncode}, "
                        f"decision {decision}). {r.stderr.strip()[:200]}")


# ====================================================================== pruning unused tools

def prune_tools(p: Product, keep: list[str], out_notes: list, out_problems: list) -> None:
    """Delete adapter files of tools not in `keep`. Deleting files is an Approval Gate (DESIGN §15.3)."""
    from .render import ALL_TOOLS, tool_files

    if not p.store.exists():
        out_problems.append("--prune needs a product (its approval is recorded in .flamin/approvals.json).")
        return
    victims = []
    for t in ALL_TOOLS:
        if t not in keep:
            victims += tool_files(p.root, t)
    if not ({"codex", "cursor"} & set(keep)) and (p.root / "AGENTS.md").exists():
        victims.append("AGENTS.md")
    if not victims:
        out_notes.append("Prune: no adapter files of unused tools are present.")
        record_tools(p, keep, replace=True)
        return
    payload = "delete adapter files of unused tools\n" + "\n".join(sorted(victims))
    with p.tx() as (state, appr):
        req = ap.approved_unused(appr, "prune", payload)
        if req:
            req["consumed"] = iso()
        else:
            req = ap.open_request(p.store, state, appr, "prune", f"Delete {len(victims)} adapter file(s) of unused tools",
                                  payload, [f"1. Understood: this product uses only {', '.join(keep)}",
                                            f"2. Planned:    delete the adapter files of the other tools ({len(victims)} files)",
                                            "3. Because:    deleting files is an Approval Gate item (DESIGN §15.3)"],
                                  data={"keep": keep})
            out(ap.format_request(req))
            out_notes.append(f"Prune waits for approval {req['id']}; nothing was deleted. After "
                             f"`flamin approve {req['id']} --yes`, run the same command again.")
            return
    for rel in sorted(victims):
        (p.root / rel).unlink(missing_ok=True)
    for d in (".claude", ".codex", ".cursor"):
        base = p.root / d
        if base.is_dir():
            for sub in sorted((x for x in base.rglob("*") if x.is_dir()), key=lambda x: len(x.parts), reverse=True):
                if not any(sub.iterdir()):
                    sub.rmdir()
            if not any(base.iterdir()):
                base.rmdir()
    tools = record_tools(p, keep, replace=True)
    p.log("prune", target=",".join(sorted(victims))[:300], reason=f"kept {tools}", approver=req.get("approver"),
          request=req["id"])
    out_notes.append(f"Pruned {len(victims)} adapter file(s) ({req['id']}). Product tools: {', '.join(tools)}.")


# ====================================================================== Cursor checks

def cursor_checks(root: Path, notes: list, problems: list, product: bool) -> None:
    """Rendered files current; the preToolUse command run the way Cursor runs it must deny; last hook call.

    Shell-level proof only: it cannot show that Cursor itself runs the hooks (that needs a live Cursor probe)."""
    from .render import cursor_local, cursor_shared

    want = dict(cursor_shared())
    want.update(cursor_local())
    stale = []
    for rel, text in sorted(want.items()):
        have = read_text(root / rel)
        if have is None:
            if rel == ".cursor/hooks.json" and not product:
                notes.append(f"Cursor: {rel} is machine-local and not rendered here; `flamin init --tool cursor` renders it.")
            else:
                problems.append(f"Cursor: {rel} is missing. Run `flamin doctor --fix --tool cursor`.")
        elif have.replace("\r\n", "\n") != text:
            stale.append(rel)
    for rel in stale:
        problems.append(f"Cursor: {rel} differs from the rendered adapter. Run `flamin doctor --fix --tool cursor`.")
    if not stale:
        present = [r for r in want if (root / r).exists()]
        notes.append(f"Cursor: {len(present)} adapter file(s) current (.cursor/agents/*.md"
                     + (", .cursor/hooks.json)." if (root / ".cursor/hooks.json").exists() else ")."))
    # Heartbeat age first: the probe below runs the hook and would reset it.
    beat = root / ".flamin" / "tmp" / "heartbeat-cursor"
    if beat.exists():
        notes.append(f"Cursor: last hook call {int(time.time() - beat.stat().st_mtime)} s ago (heartbeat-cursor).")
    elif product:
        notes.append("Cursor: no hook has run yet in this product (no heartbeat-cursor).")
    # Run the preToolUse command as Cursor runs it: from the project root, through PowerShell on Windows (probed, §5.4).
    hooks_json = json.loads(read_text(root / ".cursor" / "hooks.json") or want[".cursor/hooks.json"])
    try:
        command = hooks_json["hooks"]["preToolUse"][0]["command"]
    except (KeyError, IndexError, TypeError):
        problems.append("Cursor: no preToolUse hook in .cursor/hooks.json. Run `flamin doctor --fix --tool cursor`.")
        return
    payload = json.dumps({"hook_event_name": "preToolUse", "conversation_id": "flamin-doctor", "generation_id": "flamin-doctor",
                          "cursor_version": "flamin-doctor", "tool_name": "Shell", "tool_input": {"command": PROBE_COMMAND}})
    shell_cmd = ["powershell.exe", "-NoProfile", "-Command", command] if os.name == "nt" else ["sh", "-c", command]
    try:
        r = subprocess.run(shell_cmd, cwd=str(root), input=payload, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        problems.append(f"Cursor: the preToolUse hook command did not start ({exc}).")
        return
    try:
        decision = json.loads(r.stdout.strip().splitlines()[-1]).get("permission")
    except (ValueError, IndexError, AttributeError):
        decision = None
    # Cursor's deny contract (hooks.py, D-40): {"permission": "deny"} with exit 0.
    if r.returncode == 0 and decision == "deny":
        notes.append("Cursor: the preToolUse hook runs as Cursor runs it and denies a destructive command "
                     "(JSON deny, exit 0).")
    else:
        problems.append(f"Cursor: the preToolUse hook did not deny a destructive command (exit {r.returncode}, "
                        f"decision {decision}). {r.stderr.strip()[:200]}")
