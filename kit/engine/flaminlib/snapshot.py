"""Read-only verification of a candidate Git tree against a trusted base commit."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import boundary, gitops, locks, manifest, phase
from .profiles import Profile, REQUIRED_KEYS, classify
from .secrets_scan import find_secrets
from .statefile import STATE_FILES
from .util import FlaminError, sha256_bytes, sha256_text

COMMIT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
REGULAR = {"100644", "100755"}


def _git(root: Path, *args: str, binary: bool = False):
    return gitops.git(root, "--no-replace-objects", *args, binary=binary)


def _entries(root: Path, tree: str) -> dict[str, tuple[str, str]]:
    entries = {}
    for item in _git(root, "ls-tree", "-r", "-z", tree, binary=True).split(b"\0"):
        if item:
            meta, path = item.split(b"\t", 1)
            mode, _, oid = meta.decode("ascii").split()
            entries[path.decode("utf-8")] = (mode, oid)
    return entries


def _blob(root: Path, entries: dict, path: str) -> bytes | None:
    entry = entries.get(path)
    if entry is None:
        return None
    if entry[0] not in REGULAR:
        raise FlaminError(f"{path}: expected a regular file in the snapshot.")
    return _git(root, "cat-file", "blob", entry[1], binary=True)


def _json(root: Path, entries: dict, path: str) -> dict:
    value = json.loads(_blob(root, entries, path) or "{}")
    if not isinstance(value, dict):
        raise FlaminError(f"{path}: expected a JSON object.")
    return value


def _profiles(root: Path, base: dict) -> list[Profile]:
    profiles = []
    for entry in _json(root, base, ".flamin/stack.json").get("profiles", []):
        name = entry["name"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise FlaminError("Invalid approved profile name in trusted base.")
        path = f".flamin/stacks/{name}/profile.json"
        data = _json(root, base, path)
        if not set(REQUIRED_KEYS) <= data.keys():
            raise FlaminError(f"{path}: approved profile is missing required fields.")
        profile_root = entry.get("root", "")
        if profile_root:
            profile_root = profile_root.rstrip("/") + "/"
        profiles.append(Profile(name=data["name"], data=data, folder=Path(path).parent, root=profile_root))
    return sorted(profiles, key=lambda profile: len(profile.root), reverse=True)


def verify_snapshot(root: Path, trusted_base: str, candidate: str | None = None) -> dict:
    """Verify only Git objects. With no candidate, freeze the index with write-tree."""
    if not COMMIT_ID.fullmatch(trusted_base) or (candidate is not None and not COMMIT_ID.fullmatch(candidate)):
        raise FlaminError("Use full immutable commit IDs for the trusted base and candidate.")
    base_tree = _git(root, "rev-parse", "--verify", trusted_base + "^{commit}^{tree}").strip()
    tree = (_git(root, "rev-parse", "--verify", candidate + "^{commit}^{tree}").strip()
            if candidate is not None else _git(root, "write-tree").strip())
    base, proposed = _entries(root, base_tree), _entries(root, tree)
    changed = sorted(path for path in base.keys() | proposed.keys() if base.get(path) != proposed.get(path))
    state = _json(root, base, ".flamin/state.json")
    modules = _json(root, base, ".flamin/modules.json")
    trusted_locks = _json(root, base, ".flamin/locks.json").get("files", {})
    profiles = _profiles(root, base)
    problems = []
    engine_records = {f".flamin/{name}.json{ext}" for name in STATE_FILES for ext in ("", ".sha256")}

    for path in sorted(engine_records):
        if path in proposed and path.endswith(".json"):
            content = _blob(root, proposed, path).replace(b"\r\n", b"\n")
            side = _blob(root, proposed, path + ".sha256") or b""
            if side.strip() != sha256_bytes(content).encode("ascii"):
                problems.append(f"{path}: state checksum mismatch.")

    for path in changed:
        if path in engine_records or path.startswith(".flamin/stacks/"):
            problems.append(f"{path}: protected engine changes require independently verified engine evidence.")
            continue
        if manifest.is_kit_path(path) or path == "kit/MANIFEST":
            problems.append(f"{path}: kit changes require independently verified maintenance or upgrade evidence.")
            continue
        if any(entries.get(path, ("100644",))[0] not in REGULAR for entries in (base, proposed)):
            problems.append(f"{path}: symlink, submodule or special file requires separate verification.")
            continue

        old, new = _blob(root, base, path), _blob(root, proposed, path)
        record = trusted_locks.get(path, {})
        info = classify(path, profiles)
        try:
            old_text = old.decode("utf-8") if old is not None else None
            new_text = new.decode("utf-8") if new is not None else None
        except UnicodeDecodeError:
            if record or info.is_source:
                problems.append(f"{path}: locked and source files must be UTF-8.")
            continue

        level = record.get("level") or locks.level_of(old_text)
        if level:
            ok, why = locks.check_lock(path, old_text, new_text, {"level": level})
            if not ok:
                problems.append(why)
            if old is None or (new is not None and base[path][0] != proposed[path][0]):
                problems.append(f"{path}: protected file mode or presence changed.")
        elif locks.level_of(new_text):
            problems.append(f"{path}: new generated output requires independently verified regeneration evidence.")

        if new_text is not None:
            problems.extend(f"{path}: {why}" for why in boundary.check_boundary(path, new_text, profiles, modules))
            if find_secrets(new_text, include_passwords=not info.is_test):
                problems.append(f"{path}: possible secret in candidate content.")
            if state:
                allowed, why = phase.check_path_phase(info, path, state, mode="commit",
                                                     new_hash=sha256_text(locks.norm(new_text)))
                if not allowed:
                    problems.append(f"{path}: {why}")

    return {"base": trusted_base, "candidate_tree": tree, "changed_files": len(changed), "problems": problems}
