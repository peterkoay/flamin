"""Small shared helpers: paths, time, hashing, errors."""
from __future__ import annotations

import datetime as _dt
import fnmatch
import hashlib
import json
import os
import re
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
KIT_DIR = ENGINE_DIR.parent


class FlaminError(Exception):
    """A clean, user-facing failure. Exit code 1 unless set."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def project_root() -> Path:
    """The project root is the folder that holds kit/. FLAMIN_ROOT overrides (tests)."""
    env = os.environ.get("FLAMIN_ROOT")
    if env:
        return Path(env).resolve()
    return KIT_DIR.parent


def now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def iso(ts: _dt.datetime | None = None) -> str:
    return (ts or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return now().strftime("%Y-%m-%d")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def rel(path, root: Path | None = None) -> str:
    """Project-relative POSIX path. Accepts absolute or relative input."""
    root = root or project_root()
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    try:
        r = os.path.relpath(os.path.normpath(str(p)), os.path.normpath(str(root)))
    except ValueError:  # different drive on Windows
        return str(p).replace("\\", "/")
    return r.replace("\\", "/")


def is_inside(relpath: str) -> bool:
    return not (relpath.startswith("../") or relpath == ".." or re.match(r"^[A-Za-z]:", relpath) or relpath.startswith("/"))


def read_text(path: Path) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return None
    except (UnicodeDecodeError, IsADirectoryError, PermissionError):
        return None


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def glob_match(relpath: str, pattern: str) -> bool:
    """Glob with ** meaning any number of folders. Both sides POSIX."""
    regex = _glob_to_regex(pattern)
    return re.fullmatch(regex, relpath) is not None


_GLOB_CACHE: dict[str, str] = {}


def _glob_to_regex(pattern: str) -> str:
    if pattern in _GLOB_CACHE:
        return _GLOB_CACHE[pattern]
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    _GLOB_CACHE[pattern] = "".join(out)
    return _GLOB_CACHE[pattern]


def any_glob(relpath: str, patterns) -> bool:
    return any(glob_match(relpath, p) for p in patterns)


def fn_any(name: str, patterns) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)
