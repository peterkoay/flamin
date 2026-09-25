"""Lock levels, inline markers, locks.json cache and check-lock (DESIGN §12)."""
from __future__ import annotations

import difflib
import os
import re
from pathlib import Path

from .util import project_root, read_text, rel, sha256_text

FULLY = "FULLY_LOCKED"
STRUCTURE = "STRUCTURE_LOCKED"
LOCK_RX = re.compile(r"FLAMIN:LOCK\s+(FULLY_LOCKED|STRUCTURE_LOCKED)\b")
EXT_START_RX = re.compile(r"FLAMIN:EXTENSION\s+([A-Za-z_][\w\-]*)")
EXT_END_RX = re.compile(r"FLAMIN:EXTENSION:END\b")
HEADER_LINES = 10

SKIP_DIRS = {".git", ".flamin", "kit", "node_modules", "target", "build", "dist", ".venv", "venv",
             "__pycache__", ".gradle", ".idea", ".vscode", ".claude", ".codex", ".cursor", "Pods",
             ".expo", ".pytest_cache", "docs"}


def norm(text: str | None) -> str | None:
    return None if text is None else text.replace("\r\n", "\n")


def level_of(text: str | None) -> str | None:
    if not text:
        return None
    for line in norm(text).split("\n")[:HEADER_LINES]:
        m = LOCK_RX.search(line)
        if m:
            return m.group(1)
    return None


class MarkerError(ValueError):
    pass


def extensions(text: str) -> list[tuple[str, int, int]]:
    """(name, start line index, end line index) for each extension block. 0-based."""
    out, open_name, open_at = [], None, -1
    for i, line in enumerate(norm(text).split("\n")):
        if EXT_END_RX.search(line):
            if open_name is None:
                raise MarkerError(f"line {i + 1}: FLAMIN:EXTENSION:END without a start")
            out.append((open_name, open_at, i))
            open_name = None
            continue
        m = EXT_START_RX.search(line)
        if m:
            if open_name is not None:
                raise MarkerError(f"line {i + 1}: extension '{m.group(1)}' starts inside '{open_name}'")
            open_name, open_at = m.group(1), i
    if open_name is not None:
        raise MarkerError(f"extension '{open_name}' has no FLAMIN:EXTENSION:END")
    return out


def skeleton(text: str) -> str:
    """The file with every extension body replaced by a placeholder."""
    lines = norm(text).split("\n")
    blocks = extensions(text)
    out, i = [], 0
    for name, s, e in blocks:
        out.extend(lines[i:s + 1])
        out.append(f"\x00body:{name}")
        i = e
    out.extend(lines[i:])
    return "\n".join(out)


def bodies(text: str) -> dict[str, str]:
    lines = norm(text).split("\n")
    return {name: "\n".join(lines[s + 1:e]) for name, s, e in extensions(text)}


def outside_lines(old: str, new: str) -> list[int]:
    """1-based line numbers in the new text that changed outside extension bodies."""
    old_l, new_l = norm(old).split("\n"), norm(new).split("\n")
    try:
        inside_new = set()
        for _, s, e in extensions(new):
            inside_new.update(range(s + 1, e))
    except MarkerError:
        inside_new = set()
    bad = []
    sm = difflib.SequenceMatcher(a=old_l, b=new_l, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        rng = range(j1, j2) if j2 > j1 else [j1]
        bad.extend(j + 1 for j in rng if j not in inside_new)
    return sorted(set(bad))


def _ranges(nums: list[int]) -> str:
    if not nums:
        return "?"
    parts, start, prev = [], nums[0], nums[0]
    for n in nums[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}" if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    return ", ".join(parts)


def check_lock(relpath: str, old: str | None, new: str | None, entry: dict | None,
               regenerating: set[str] | None = None) -> tuple[bool, str]:
    """Decide if a change from old to new (None = missing/deleted) may land.

    entry is the locks.json record for this path, if any. Returns (allowed, reason).
    """
    old, new = norm(old), norm(new)
    if regenerating and relpath in regenerating:
        return True, "regeneration in progress"
    level = (entry or {}).get("level") or level_of(old)
    if level is None:
        return True, "not locked"
    if new is None:
        return False, f"{level}: {relpath} is locked and may not be deleted. Only `flamin generate` changes it."
    if level == FULLY:
        if new == old or sha256_text(new) == (entry or {}).get("content_hash"):
            return True, "unchanged locked file"
        hint = (entry or {}).get("hint") or "an extension point in the matching STRUCTURE_LOCKED file"
        return False, (f"FULLY_LOCKED: {relpath} is generated base code. Only `flamin generate` may change it. "
                       f"Put hand-written logic in {hint}.")
    # STRUCTURE_LOCKED
    try:
        new_skel = skeleton(new)
    except MarkerError as exc:
        return False, f"STRUCTURE_LOCKED: {relpath}: extension markers broken ({exc})"
    if level_of(new) != STRUCTURE:
        return False, f"STRUCTURE_LOCKED: {relpath}: the FLAMIN:LOCK marker line may not change"
    try:
        old_skel = skeleton(old) if old is not None else None
    except MarkerError:
        old_skel = None
    if new_skel == old_skel or sha256_text(new_skel) == (entry or {}).get("skeleton_hash"):
        return True, "changes only inside extension bodies"
    names = [n for n, _, _ in extensions(new)]
    lines = outside_lines(old or "", new)
    where = f" (names: {', '.join(names)})" if names else ""
    many = len(lines) != 1
    return False, (f"STRUCTURE_LOCKED: {relpath}: line{'s' if many else ''} {_ranges(lines)} "
                   f"{'are' if many else 'is'} outside every extension body. "
                   f"Only edit between FLAMIN:EXTENSION and FLAMIN:EXTENSION:END{where}.")


def iter_code_files(root: Path, extensions_: set[str]):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".flamin-backup")]
        for fn in filenames:
            if not extensions_ or any(fn.endswith(e) for e in extensions_):
                yield Path(dirpath) / fn


def rebuild(root: Path | None, old_locks: dict, extensions_: set[str]) -> dict:
    """Rebuild locks.json from inline markers, keeping generator hashes where the file still matches."""
    root = root or project_root()
    old_files = old_locks.get("files", {})
    files = {}
    for p in iter_code_files(root, extensions_):
        text = read_text(p)
        level = level_of(text)
        if not level:
            continue
        rp = rel(p, root)
        prev = dict(old_files.get(rp, {}))
        prev["level"] = level
        try:
            prev["extensions"] = [n for n, _, _ in extensions(text)]
        except MarkerError as exc:
            prev["extensions"] = []
            prev["marker_error"] = str(exc)
        prev.setdefault("content_hash", sha256_text(norm(text)))
        if level == STRUCTURE and "marker_error" not in prev:
            prev.setdefault("skeleton_hash", sha256_text(skeleton(text)))
        files[rp] = prev
    return {"files": dict(sorted(files.items()))}
