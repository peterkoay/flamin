"""Stack profiles: load, and classify a project path (DESIGN §10, §11)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .util import KIT_DIR, FlaminError, any_glob, project_root

REQUIRED_KEYS = ("name", "targets", "layers", "layer_dirs", "may_use", "module_root",
                 "extensions", "lock_comment", "import_patterns", "test_patterns", "commands")


@dataclass
class Profile:
    name: str
    data: dict
    folder: Path
    root: str = ""  # product folder this profile covers, POSIX, "" or ending in "/"
    _module_rx: re.Pattern | None = field(default=None, repr=False)

    @property
    def layers(self) -> list[str]:
        return list(self.data.get("layers", []))

    @property
    def layered(self) -> bool:
        return bool(self.layers)

    @property
    def extensions(self) -> list[str]:
        return list(self.data.get("extensions", []))

    @property
    def lock_comment(self) -> str:
        return self.data.get("lock_comment", "#")

    @property
    def layer_dirs(self) -> dict:
        return dict(self.data.get("layer_dirs", {}))

    @property
    def contracts_dir(self) -> str | None:
        return self.layer_dirs.get("contracts")

    def dir_to_layer(self, d: str) -> str | None:
        for layer, folder in self.layer_dirs.items():
            if folder == d:
                return layer
        return None

    def may_use(self, src_layer: str, dst_layer: str) -> bool:
        if src_layer == dst_layer:
            return True
        return dst_layer in self.data.get("may_use", {}).get(src_layer, [])

    def module_regex(self) -> re.Pattern:
        if self._module_rx is None:
            tpl = self.data.get("module_root", "modules/<module>/")
            parts = tpl.split("<module>")
            rx = re.escape(self.root + parts[0]) + r"(?P<module>[A-Za-z0-9_\-]+)/(?P<rest>.*)"
            self._module_rx = re.compile(rx)
        return self._module_rx

    def module_dir(self, module: str) -> str:
        return self.root + self.data.get("module_root", "modules/<module>/").replace("<module>", module)

    def is_test(self, relpath: str) -> bool:
        sub = relpath[len(self.root):] if relpath.startswith(self.root) else relpath
        return any_glob(sub, self.data.get("test_patterns", []))

    def is_source(self, relpath: str) -> bool:
        return any(relpath.endswith(ext) for ext in self.extensions)


def load_profile_dir(folder: Path, root: str = "") -> Profile:
    pj = folder / "profile.json"
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FlaminError(f"Stack profile not found: {folder.name} (no profile.json)")
    except ValueError as exc:
        raise FlaminError(f"Stack profile {folder.name}: profile.json is not valid JSON ({exc})")
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise FlaminError(f"Stack profile {folder.name}: missing keys {missing}")
    if root and not root.endswith("/"):
        root += "/"
    return Profile(name=data["name"], data=data, folder=folder, root=root)


def kit_profiles() -> list[str]:
    base = KIT_DIR / "stacks"
    return sorted(p.name for p in base.iterdir() if (p / "profile.json").exists()) if base.exists() else []


def kit_profile(name: str) -> Profile:
    return load_profile_dir(KIT_DIR / "stacks" / name)


def product_profiles(root: Path | None = None) -> list[Profile]:
    """Profiles approved for this product, from stack.json and `.flamin/stacks/`."""
    root = root or project_root()
    sj = root / ".flamin" / "stack.json"
    if not sj.exists():
        return []
    stack = json.loads(sj.read_text(encoding="utf-8"))
    out = []
    for entry in stack.get("profiles", []):
        folder = root / ".flamin" / "stacks" / entry["name"]
        out.append(load_profile_dir(folder, entry.get("root", "")))
    # Longest root first so nested roots win.
    out.sort(key=lambda p: len(p.root), reverse=True)
    return out


@dataclass
class PathInfo:
    path: str
    profile: Profile | None = None
    module: str | None = None
    layer: str | None = None
    is_test: bool = False
    is_source: bool = False

    @property
    def is_product_code(self) -> bool:
        return self.profile is not None and self.is_source and not self.is_test


NOT_PRODUCT = ("kit/", ".flamin/", ".git/", ".claude/", ".codex/", ".cursor/", "docs/", "node_modules/",
               ".flamin-backup")


def classify(relpath: str, profiles: list[Profile]) -> PathInfo:
    info = PathInfo(path=relpath)
    if relpath.startswith(NOT_PRODUCT) or relpath in ("flamin", "flamin.cmd"):
        return info  # the kit, engine state, tool config and docs are never product code
    for prof in profiles:
        if prof.root and not relpath.startswith(prof.root):
            continue
        info.profile = prof
        info.is_test = prof.is_test(relpath)
        info.is_source = prof.is_source(relpath)
        m = prof.module_regex().fullmatch(relpath)
        if m:
            info.module = m.group("module")
            first = m.group("rest").split("/", 1)[0]
            info.layer = prof.dir_to_layer(first)
        return info
    return info
