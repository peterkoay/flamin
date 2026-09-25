"""kit/MANIFEST: a hash of every kit file (DESIGN §15.1, P-19/D-30). Heuristic backstop."""
from __future__ import annotations

from pathlib import Path

from .util import sha256_text

HEADER = "# flamin kit manifest: sha256 of each kit file (line endings normalised to LF). Written by `flamin doctor --kit --update-manifest` or `flamin upgrade`.\n"
LAUNCHERS = ("flamin", "flamin.cmd")
SKIP_PARTS = {"__pycache__"}


def kit_files(root: Path) -> list[str]:
    kit = root / "kit"
    files = []
    for p in sorted(kit.rglob("*")):
        if p.is_file() and not (set(p.parts) & SKIP_PARTS) and p.name != "MANIFEST" and p.suffix != ".pyc":
            files.append(p.relative_to(root).as_posix())
    files += [n for n in LAUNCHERS if (root / n).exists()]
    return files


def content_hash(data: bytes) -> str:
    return sha256_text(data.decode("utf-8", "surrogateescape").replace("\r\n", "\n"))


def build(root: Path) -> str:
    lines = [HEADER]
    for rp in kit_files(root):
        lines.append(f"{content_hash((root / rp).read_bytes())}  {rp}\n")
    return "".join(lines)


def parse(text: str | None) -> dict[str, str]:
    out = {}
    for line in (text or "").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        h, _, path = line.partition("  ")
        out[path.strip()] = h.strip()
    return out


def is_kit_path(rp: str) -> bool:
    return (rp.startswith("kit/") and not rp.endswith("/MANIFEST") and rp != "kit/MANIFEST") or rp in LAUNCHERS


def verify_tree(root: Path) -> list[str]:
    want = parse((root / "kit" / "MANIFEST").read_text(encoding="utf-8") if (root / "kit" / "MANIFEST").exists() else "")
    if not want:
        return ["kit/MANIFEST is missing or empty"]
    problems = []
    have = set(kit_files(root))
    for rp in sorted(have):
        h = content_hash((root / rp).read_bytes())
        if rp not in want:
            problems.append(f"{rp}: kit file not in kit/MANIFEST")
        elif want[rp] != h:
            problems.append(f"{rp}: changed (hash does not match kit/MANIFEST)")
    for rp in sorted(set(want) - have):
        problems.append(f"{rp}: listed in kit/MANIFEST but missing")
    return problems

