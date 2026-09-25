"""flamin-generate (DESIGN §14.2). Deterministic: same spec and templates give byte-identical output."""
from __future__ import annotations

import json
import re
import string
import tomllib
from pathlib import Path

from . import locks as lk
from .profiles import Profile
from .statefile import atomic_write_bytes
from .util import FlaminError, read_text, sha256_text

KINDS = ("write", "query")


# ------------------------------------------------------------ naming helpers

def words(name: str) -> list[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [w.lower() for w in re.split(r"[\s_\-./]+", s) if w]


def snake(name: str) -> str:
    return "_".join(words(name))


def kebab(name: str) -> str:
    return "-".join(words(name))


def pascal(name: str) -> str:
    return "".join(w.capitalize() for w in words(name))


def camel(name: str) -> str:
    p = pascal(name)
    return p[:1].lower() + p[1:]


# ------------------------------------------------------------ loading

def load_manifest(prof: Profile) -> dict:
    mf = prof.folder / "templates" / "manifest.json"
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FlaminError(f"Profile {prof.name} has no templates/manifest.json")


def template_hash(prof: Profile) -> str:
    parts = []
    for p in sorted((prof.folder / "templates").rglob("*")):
        if p.is_file():
            parts.append(f"{p.relative_to(prof.folder).as_posix()}:{sha256_text(lk.norm(read_text(p) or ''))}")
    return sha256_text("\n".join(parts))


def load_spec(path: Path) -> dict:
    try:
        return tomllib.loads(read_text(path) or "")
    except tomllib.TOMLDecodeError as exc:
        raise FlaminError(f"{path.name}: not valid TOML ({exc})")


def load_model(fdir: Path, module: str) -> dict:
    mp = fdir / "design" / module / "model.toml"
    if not mp.exists():
        raise FlaminError(f"Data model missing: .flamin/design/{module}/model.toml")
    return tomllib.loads(read_text(mp))


def spec_hash(spec_path: Path, model_path: Path) -> str:
    return sha256_text(lk.norm(read_text(spec_path) or "") + "\n--model--\n" + lk.norm(read_text(model_path) or ""))


# ------------------------------------------------------------ rendering

class Renderer:
    def __init__(self, prof: Profile, manifest: dict):
        self.prof = prof
        self.m = manifest
        self.tdir = prof.folder / "templates"

    def ftype(self, t: str) -> str:
        types = self.m.get("types", {})
        if t not in types:
            raise FlaminError(f"Profile {self.prof.name}: field type '{t}' has no template mapping (template gap).")
        return types[t]

    def field_vars(self, f: dict) -> dict:
        lit = self.m.get("literals", {"true": "true", "false": "false"})
        t = f.get("type", "string")
        return {
            "name": f["name"], "name_snake": snake(f["name"]), "name_camel": camel(f["name"]),
            "Name": pascal(f["name"]), "type": self.ftype(t), "raw_type": t,
            "required": lit["true"] if f.get("required") else lit["false"],
            "default": self.m.get("defaults", {}).get(t, "None"),
        }

    def loops(self, lists: dict) -> dict:
        out = {}
        for key, spec in self.m.get("loops", {}).items():
            items = lists.get(spec["over"], [])
            if spec.get("where"):
                k, v = spec["where"].split("=")
                items = [i for i in items if str(bool(i.get(k))).lower() == v]
            if not items:
                out[key] = spec.get("empty", "")
                continue
            tpl = string.Template(spec["each"])
            rendered = []
            for i, f in enumerate(items):
                vars_ = self.field_vars(f)
                vars_["sep"] = "" if i == len(items) - 1 else spec.get("sep", "")
                rendered.append(tpl.substitute(vars_))
            out[key] = spec.get("join", "\n").join(rendered)
        return out

    def render_file(self, template: str, vars_: dict) -> str:
        text = lk.norm(read_text(self.tdir / template))
        if text is None:
            raise FlaminError(f"Profile {self.prof.name}: template {template} missing")
        try:
            return string.Template(text).substitute(vars_)
        except KeyError as exc:
            raise FlaminError(f"Template {template}: unknown variable {exc}")

    def path(self, tpl: str, vars_: dict) -> str:
        return string.Template(tpl).substitute(vars_)


def build_vars(prof: Profile, module: str, spec: dict | None, model: dict, entity_name: str) -> tuple[dict, dict]:
    ent = next((e for e in model.get("entities", []) if e.get("name") == entity_name), None)
    if ent is None:
        raise FlaminError(f"Entity '{entity_name}' is not in .flamin/design/{module}/model.toml")
    v = {
        "module": module, "Module": pascal(module), "module_camel": camel(module),
        "module_dir": prof.module_dir(module), "root": prof.root,
        "entity": entity_name, "Entity": pascal(entity_name), "entity_snake": snake(entity_name),
        "entity_camel": camel(entity_name), "entity_kebab": kebab(entity_name),
        "id_field": ent.get("id", "id"),
    }
    lists = {"entity_fields": ent.get("fields", []),
             "stored_fields": [f for f in ent.get("fields", []) if f.get("name") != ent.get("id", "id")]}
    if spec is not None:
        name = spec["name"]
        api = spec.get("api", {})
        v.update({
            "feature": name, "Feature": pascal(name), "feature_snake": snake(name), "feature_camel": camel(name),
            "feature_kebab": kebab(name), "method": api.get("method", "POST").upper(),
            "method_lower": api.get("method", "POST").lower(), "api_path": api.get("path", f"/api/{module}/{kebab(name)}"),
        })
        lists["input_fields"] = api.get("input", [])
        efield = {f["name"]: f for f in ent.get("fields", [])}
        outs = api.get("output", []) or [f for f in ent.get("fields", []) if not f.get("secret")]
        lists["output_fields"] = [{**efield.get(o["name"], {}), **o} for o in outs]
        lists["filter_fields"] = [f for f in api.get("input", []) if f["name"] in spec.get("query", {}).get("filter", [])]
        names = {f["name"] for f in ent.get("fields", [])}
        lists["stored_inputs"] = [f for f in api.get("input", []) if f["name"] in names and f["name"] != v["id_field"]]
    return v, lists


# ------------------------------------------------------------ main entry

def generate(root: Path, fdir: Path, prof: Profile, module: str, spec_path: Path, old_locks: dict) -> dict:
    """Generate files for one spec. Returns {'written': [...], 'orphans': [...], 'locks': new_locks}."""
    manifest = load_manifest(prof)
    spec = load_spec(spec_path)
    spec.setdefault("name", spec_path.stem)
    kind = spec.get("kind", "write")
    if kind not in KINDS:
        raise FlaminError(f"{spec_path.name}: kind must be one of {KINDS}")
    model = load_model(fdir, module)
    rend = Renderer(prof, manifest)
    s_hash = spec_hash(spec_path, fdir / "design" / module / "model.toml")
    t_hash = template_hash(prof)
    spec_rel = spec_path.relative_to(root).as_posix()

    jobs = []  # (relpath, text, lock level or None, scaffold?)
    ev, el = build_vars(prof, module, None, model, spec.get("entity", ""))
    ev.update(rend.loops(el))
    for item in manifest.get("shared", []):
        jobs.append((rend.path(item["path"], ev), rend.render_file(item["template"], ev), item.get("lock"), False))
    for item in manifest.get("scaffold", []):
        jobs.append((rend.path(item["path"], ev), rend.render_file(item["template"], ev), None, True))
    for item in manifest.get("entity", []):
        jobs.append((rend.path(item["path"], ev), rend.render_file(item["template"], ev), item.get("lock"), False))
    fv, fl = build_vars(prof, module, spec, model, spec.get("entity", ""))
    fv.update(rend.loops(fl))
    for item in manifest.get(kind, []):
        jobs.append((rend.path(item["path"], fv), rend.render_file(item["template"], fv), item.get("lock"), False))

    regen = fdir / "tmp" / "regenerating.lock"
    regen.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(regen, json.dumps([j[0] for j in jobs]).encode())
    written, orphans, new_entries = [], [], {}
    try:
        for relpath, text, level, scaffold in jobs:
            target = root / relpath
            old = lk.norm(read_text(target))
            if scaffold and old is not None:
                continue  # open file: created once, then owned by people
            if level == lk.STRUCTURE and old is not None:
                text, lost = _carry_bodies(old, text)
                for name, body in lost.items():
                    od = fdir / "tmp" / "orphans"
                    od.mkdir(parents=True, exist_ok=True)
                    of = od / f"{relpath.replace('/', '__')}.{name}.txt"
                    atomic_write_bytes(of, body.encode("utf-8"))
                    orphans.append(f"{relpath}: extension '{name}' no longer in the spec; body kept in {of.relative_to(root).as_posix()}")
            if level and lk.level_of(text) != level:
                raise FlaminError(f"Template for {relpath} lacks its FLAMIN:LOCK {level} marker")
            if old != text:
                atomic_write_bytes(target, text.encode("utf-8"))
                written.append(relpath)
            if level:
                entry = {"level": level, "spec": spec_rel, "spec_hash": s_hash, "template_hash": t_hash,
                         "content_hash": sha256_text(text), "profile": prof.name,
                         "extensions": [n for n, _, _ in lk.extensions(text)]}
                if level == lk.STRUCTURE:
                    entry["skeleton_hash"] = sha256_text(lk.skeleton(text))
                else:
                    shell = next((j[0] for j in jobs if j[2] == lk.STRUCTURE), None)
                    if shell:
                        entry["hint"] = f"the extension points in {shell}"
                new_entries[relpath] = entry
    finally:
        try:
            regen.unlink()
        except FileNotFoundError:
            pass
    files = dict(old_locks.get("files", {}))
    files.update(new_entries)
    return {"written": written, "orphans": orphans, "locks": {"files": dict(sorted(files.items()))},
            "all": [j[0] for j in jobs]}


def _carry_bodies(old: str, new: str) -> tuple[str, dict]:
    """Copy each named extension body from the old file into the new shell."""
    try:
        old_lines = old.split("\n")
        old_bodies = {n: old_lines[s + 1:e] for n, s, e in lk.extensions(old)}
    except lk.MarkerError:
        return new, {}
    lines = new.split("\n")
    blocks = lk.extensions(new)
    out, i = [], 0
    for name, s, e in blocks:
        out.extend(lines[i:s + 1])
        out.extend(old_bodies.get(name, lines[s + 1:e]))
        i = e
    out.extend(lines[i:])
    new_names = {n for n, _, _ in blocks}
    lost = {n: "\n".join(b) for n, b in old_bodies.items() if n not in new_names and "\n".join(b).strip()}
    return "\n".join(out), lost
