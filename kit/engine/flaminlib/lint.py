"""flamin-lint (DESIGN §14.3): spec checks, --portability and --drift."""
from __future__ import annotations

import getpass
import os
import platform
import re
import tomllib
from pathlib import Path

from . import generate as gen
from . import locks as lk
from .util import KIT_DIR, read_text

PRIMITIVES = {"string", "int", "float", "bool"}
ROLE_WORDS = ("Model", "Entity", "Record", "Row", "Dto", "DO", "PO")


def lint_spec(p, spec_path: Path) -> list[str]:
    """Return problems for one design spec. Empty list means clean."""
    problems: list[str] = []
    rp = p.relpath(spec_path)
    parts = rp.split("/")
    if len(parts) < 4 or parts[0] != ".flamin" or parts[1] != "design":
        return [f"{rp}: specs live in .flamin/design/<module>/<name>.toml"]
    module = parts[2]
    if spec_path.name == "model.toml":
        return _lint_model(p, spec_path)
    try:
        spec = tomllib.loads(read_text(spec_path) or "")
    except tomllib.TOMLDecodeError as exc:
        return [f"{rp}: not valid TOML ({exc})"]
    try:
        model = gen.load_model(p.fdir, module)
    except Exception as exc:  # noqa: BLE001 - reported as a lint problem
        return [f"{rp}: {exc}"]
    kind = spec.get("kind", "write")
    if kind not in gen.KINDS:
        problems.append(f"{rp}: kind must be 'write' or 'query'")
        return problems
    entities = {e.get("name"): e for e in model.get("entities", [])}
    ent = entities.get(spec.get("entity"))
    if ent is None:
        problems.append(f"{rp}: entity '{spec.get('entity')}' is not in the data model ({', '.join(entities) or 'none'})")
        return problems
    efields = {f["name"]: f for f in ent.get("fields", [])}
    api = spec.get("api", {})
    if not api.get("path") or not api.get("method"):
        problems.append(f"{rp}: [api] needs method and path")
    inputs = api.get("input", [])
    for f in inputs:
        t = f.get("type", "string")
        if t not in PRIMITIVES and not t.startswith("enum:"):
            problems.append(f"{rp}: input '{f.get('name')}' has type '{t}'. The interface accepts only write input, "
                            "query input, plain values and enums (no storage models or internal objects).")
        if any(t.endswith(w) for w in ROLE_WORDS) or t in entities:
            problems.append(f"{rp}: input '{f.get('name')}' uses an internal object role ({t})")
    transient = set(spec.get("write_plan", {}).get("transient", []))
    if kind == "write":
        wp = spec.get("write_plan", {})
        root = wp.get("aggregate_root")
        if not root:
            problems.append(f"{rp}: [write_plan] needs aggregate_root")
        elif not entities.get(root, {}).get("aggregate_root"):
            problems.append(f"{rp}: write_plan.aggregate_root '{root}' is not marked aggregate_root in the data model")
        for f in inputs:
            if f["name"] not in efields and f["name"] not in transient:
                problems.append(f"{rp}: write input '{f['name']}' is not a field of {ent['name']} "
                                "(list it in write_plan.transient if it is not stored)")
    else:
        for name in spec.get("query", {}).get("filter", []):
            if name not in efields:
                problems.append(f"{rp}: query filter '{name}' is not a field of {ent['name']}")
            if name not in [f["name"] for f in inputs]:
                problems.append(f"{rp}: query filter '{name}' is not a query input")
    for f in api.get("output", []):
        if f["name"] not in efields:
            problems.append(f"{rp}: view output '{f['name']}' is not a field of {ent['name']}")
        if efields.get(f["name"], {}).get("secret"):
            problems.append(f"{rp}: view output '{f['name']}' is marked secret in the data model")
    # extension points must match the templates
    prof = _profile_for_module(p, module)
    if prof is not None:
        try:
            manifest = gen.load_manifest(prof)
            known = set(manifest.get("extension_points", {}).get(kind, []))
            for ep in spec.get("extension_points", []):
                if ep.get("name") not in known:
                    problems.append(f"{rp}: extension point '{ep.get('name')}' is not in the {prof.name} "
                                    f"{kind} templates ({', '.join(sorted(known))})")
            for f in inputs + ent.get("fields", []):
                if f.get("type", "string") not in manifest.get("types", {}) and not str(f.get("type")).startswith("enum:"):
                    problems.append(f"{rp}: type '{f.get('type')}' has no template in {prof.name} (template gap)")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{rp}: {exc}")
    mods = p.modules()
    for call in spec.get("calls", []):
        other = call.get("module")
        if other not in mods:
            problems.append(f"{rp}: calls unknown module '{other}'")
        elif other != module and other not in mods.get(module, {}).get("depends", []):
            problems.append(f"{rp}: calls '{other}' but the module map does not list it as a dependency")
        elif other != module and not call.get("contract"):
            problems.append(f"{rp}: a call to '{other}' must name a contract (cross-module calls go only through contracts)")
    return problems


def _lint_model(p, path: Path) -> list[str]:
    rp = p.relpath(path)
    try:
        data = tomllib.loads(read_text(path) or "")
    except tomllib.TOMLDecodeError as exc:
        return [f"{rp}: not valid TOML ({exc})"]
    problems = []
    for e in data.get("entities", []):
        if not e.get("name"):
            problems.append(f"{rp}: an entity has no name")
        for f in e.get("fields", []):
            if f.get("type", "string") not in PRIMITIVES and not str(f.get("type")).startswith("enum:"):
                problems.append(f"{rp}: {e.get('name')}.{f.get('name')} has unknown type {f.get('type')}")
    return problems


def _profile_for_module(p, module: str):
    entry = p.modules().get(module, {})
    for prof in p.profiles():
        if prof.name == entry.get("profile"):
            return prof
    return p.profiles()[0] if p.profiles() else None


def drift_problems(p) -> list[str]:
    """Spec or template changed after generation (Heuristic)."""
    out = []
    files = p.locks().get("files", {})
    seen = set()
    for rp, entry in files.items():
        spec = entry.get("spec")
        if not spec or spec in seen:
            continue
        seen.add(spec)
        sp = p.root / spec
        module = spec.split("/")[2] if spec.count("/") >= 3 else ""
        if not sp.exists():
            out.append(f"{spec}: spec deleted but its generated files remain")
            continue
        if gen.spec_hash(sp, p.fdir / "design" / module / "model.toml") != entry.get("spec_hash"):
            out.append(f"{spec}: changed after generation; run `flamin generate {spec}`")
        prof = next((x for x in p.profiles() if x.name == entry.get("profile")), None)
        if prof and gen.template_hash(prof) != entry.get("template_hash"):
            out.append(f"{spec}: profile templates changed after generation; regenerate")
    return out


ABS_PATH = re.compile(r"(?<![\w/])(?:[A-Za-z]:\\\\?(?:Users|home|Documents)\b|/home/[a-z_][\w\-]*|/Users/[A-Za-z][\w\-]*)")


def portability_problems(root: Path, include_state: bool = True) -> list[str]:
    """Absolute paths, user names and machine names in kit and state files (Heuristic)."""
    names = set()
    for fn in (getpass.getuser, lambda: os.environ.get("USERNAME"), lambda: os.environ.get("USER"),
               platform.node, lambda: os.environ.get("COMPUTERNAME")):
        v = _safe(fn)
        if v and len(v) >= 3:
            names.add(v)
    targets = [KIT_DIR, root / "flamin", root / "flamin.cmd"]
    if include_state:
        targets.append(root / ".flamin")
    out = []
    for t in targets:
        files = [t] if t.is_file() else [f for f in t.rglob("*") if f.is_file()] if t.exists() else []
        for f in files:
            if "audit" in f.parts or "__pycache__" in f.parts or f.suffix in (".pyc",):
                continue
            text = read_text(f)
            if text is None:
                continue
            rp = f.relative_to(root).as_posix() if str(f).startswith(str(root)) else str(f)
            m = ABS_PATH.search(text)
            if m:
                out.append(f"{rp}: absolute path '{m.group(0)}'")
            for n in names:
                if re.search(r"(?<![\w])" + re.escape(n) + r"(?![\w])", text, re.IGNORECASE):
                    out.append(f"{rp}: contains the user or machine name '{n}'")
    return out


def _safe(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None


def cmd_lint(p, args) -> int:
    problems: list[str] = []
    ran = []
    if args.portability:
        problems += portability_problems(p.root)
        ran.append("portability")
    if args.drift:
        problems += drift_problems(p)
        ran.append("drift")
    if args.spec or not ran:
        specs = [Path(s) for s in args.spec] if args.spec else sorted((p.fdir / "design").rglob("*.toml"))
        for s in specs:
            s = s if s.is_absolute() else p.root / s
            problems += lint_spec(p, s)
        ran.append(f"{len(specs)} spec(s)")
        if not args.spec:
            problems += drift_problems(p)
            problems += portability_problems(p.root)
            ran += ["drift", "portability"]
    for pr in problems:
        print("  - " + pr)
    print(f"flamin lint ({', '.join(ran)}): {'clean' if not problems else f'{len(problems)} problem(s)'}")
    p.log("lint", target=",".join(ran), decision="allow" if not problems else "deny", reason=f"{len(problems)} problem(s)")
    return 0 if not problems else 1
