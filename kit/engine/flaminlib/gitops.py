"""Git helpers for the backstop (DESIGN §4.4)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .util import FlaminError


def git(root: Path, *args: str, check: bool = True, binary: bool = False):
    try:
        r = subprocess.run(["git", *args], cwd=str(root), capture_output=True, check=False)
    except FileNotFoundError:
        raise FlaminError("git is not installed or not on PATH. The backstop needs git.")
    if check and r.returncode != 0:
        raise FlaminError(f"git {' '.join(args)} failed: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def is_repo(root: Path) -> bool:
    """True only when the project root is the top of its own git repo (not a folder inside another repo)."""
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(root), capture_output=True)
    except FileNotFoundError:
        return False
    if r.returncode != 0:
        return False
    top = Path(r.stdout.decode("utf-8", "replace").strip())
    try:
        return top.resolve() == Path(root).resolve()
    except OSError:
        return False


def has_head(root: Path) -> bool:
    r = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=str(root), capture_output=True)
    return r.returncode == 0


def changes(root: Path, rng: str | None) -> list[tuple[str, str, str | None]]:
    """[(status, path, old_path)] for the staged diff or a commit range."""
    if rng:
        out = git(root, "diff", "--name-status", "-M", "--no-color", rng)
    else:
        out = git(root, "diff", "--cached", "--name-status", "-M", "--no-color")
    res = []
    for line in out.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        st = parts[0][0]
        if st == "R" and len(parts) >= 3:
            res.append(("D", parts[1], None))
            res.append(("A", parts[2], parts[1]))
        elif len(parts) >= 2:
            res.append((st, parts[1], None))
    return res


def blob(root: Path, rev: str, path: str) -> str | None:
    """File text at a revision (':' prefix-free). rev '' means the index."""
    spec = f":{path}" if rev == "" else f"{rev}:{path}"
    r = subprocess.run(["git", "show", spec], cwd=str(root), capture_output=True)
    if r.returncode != 0:
        return None
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def range_ends(rng: str) -> tuple[str, str]:
    if "..." in rng:
        a, b = rng.split("...", 1)
    elif ".." in rng:
        a, b = rng.split("..", 1)
    else:
        a, b = rng + "~1", rng
    return a or "HEAD", b or "HEAD"
