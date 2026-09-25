"""Module boundary and layer check (DESIGN §13). Heuristic: an import scan, not a parser."""
from __future__ import annotations

import posixpath
import re

from .profiles import Profile, classify


def _resolve_relative(relpath: str, target: str) -> str:
    base = posixpath.dirname(relpath)
    return posixpath.normpath(posixpath.join(base, target))


def _target(prof: Profile, relpath: str, m: re.Match, profiles) -> tuple[str | None, str | None]:
    gd = m.groupdict()
    if gd.get("module"):
        layer_dir = gd.get("layer")
        return gd["module"], prof.dir_to_layer(layer_dir) if layer_dir else None
    raw = gd.get("path")
    if raw:
        if raw.startswith("."):
            resolved = _resolve_relative(relpath, raw)
        else:
            resolved = prof.root + raw.lstrip("/")
        info = classify(resolved, [prof])
        if info.module is None:  # the path may point at the module folder itself
            info = classify(resolved.rstrip("/") + "/_", [prof])
        return info.module, info.layer
    return None, None


def check_boundary(relpath: str, content: str | None, profiles: list[Profile],
                   modules: dict | None = None) -> list[str]:
    """Return a list of violation messages. Empty list means allowed."""
    if not content:
        return []
    info = classify(relpath, profiles)
    prof = info.profile
    if prof is None or not prof.layered or info.module is None or info.is_test or not info.is_source:
        return []
    patterns = [re.compile(p, re.MULTILINE) for p in prof.data.get("import_patterns", [])]
    problems: list[str] = []
    seen = set()
    for rx in patterns:
        for m in rx.finditer(content):
            mod, layer = _target(prof, relpath, m, profiles)
            if mod is None:
                continue  # external package or shared code
            key = (mod, layer, m.group(0).strip())
            if key in seen:
                continue
            seen.add(key)
            line_no = content.count("\n", 0, m.start()) + 1
            if mod != info.module:
                if layer == "contracts":
                    if info.layer and not prof.may_use(info.layer, "contracts"):
                        problems.append(f"line {line_no}: layer '{info.layer}' may not call other modules, even through contracts")
                    continue
                contract = ((modules or {}).get(mod) or {}).get("contracts") or [prof.module_dir(mod) + (prof.contracts_dir or "contracts") + "/"]
                problems.append(
                    f"line {line_no}: '{info.module}' imports '{mod}' directly ({m.group(0).strip()}). "
                    f"Cross-module calls go only through contracts: use {contract[0]}"
                )
            elif info.layer and layer and not prof.may_use(info.layer, layer):
                problems.append(
                    f"line {line_no}: layer '{info.layer}' may not use layer '{layer}' "
                    f"(outer layers call inner layers, never the reverse)"
                )
    return problems
