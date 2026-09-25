"""State safety: one writer at a time, atomic writes, checksum sidecars (DESIGN §3.2, §3.4)."""
from __future__ import annotations

import contextlib
import json
import os
import secrets
import sys
import time
from pathlib import Path

from .util import FlaminError, canonical_json, iso, now, project_root, sha256_bytes

STATE_FILES = ("state", "locks", "modules", "stack", "models", "approvals")
LOCK_WAIT_SECONDS = 10.0
STALE_SECONDS = 120


def flamin_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / ".flamin"


def session_id() -> str:
    """Host-free session id. Hooks pass the tool's session through FLAMIN_SESSION."""
    sid = os.environ.get("FLAMIN_SESSION")
    if not sid:
        sid = "s-" + secrets.token_hex(2)
        os.environ["FLAMIN_SESSION"] = sid
    return sid


def lock_wait() -> float:
    try:
        return float(os.environ.get("FLAMIN_LOCK_WAIT", LOCK_WAIT_SECONDS))
    except ValueError:
        return LOCK_WAIT_SECONDS


class StateLock:
    """`.flamin/.lock` taken by exclusive create. Re-entrant inside one process.

    Never steals a lock. A stale lock is only cleared by `flamin doctor --clear-stale-lock`.
    """

    _depth: dict[str, int] = {}

    def __init__(self, root: Path | None = None, wait: float | None = None):
        self.path = flamin_dir(root) / ".lock"
        self.wait = lock_wait() if wait is None else wait

    def acquire(self) -> None:
        key = str(self.path)
        if self._depth.get(key):
            self._depth[key] += 1
            return
        deadline = time.monotonic() + self.wait
        info = {"pid": os.getpid(), "session": session_id(), "started": iso()}
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise FlaminError(self.busy_message(), code=4)
                time.sleep(0.05)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(info, fh)
            self._depth[key] = 1
            return

    def try_acquire(self, wait: float) -> bool:
        old = self.wait
        self.wait = wait
        try:
            self.acquire()
            return True
        except FlaminError:
            return False
        finally:
            self.wait = old

    def release(self) -> None:
        key = str(self.path)
        depth = self._depth.get(key, 0)
        if depth > 1:
            self._depth[key] = depth - 1
            return
        self._depth[key] = 0
        with contextlib.suppress(FileNotFoundError):
            os.remove(self.path)

    def busy_message(self) -> str:
        holder = read_lock_info(self.path)
        who = holder.get("session", "unknown")
        started = holder.get("started", "?")
        started_hms = started[11:19] if len(started) >= 19 else started
        return (
            f"Another flamin command is writing state (session {who}, started {started_hms}). "
            "Try again, or run `flamin doctor` if it looks stuck."
        )

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def read_lock_info(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def pid_alive(pid: int) -> bool:
    """Read-only liveness check. On Windows never use os.kill(pid, 0) (signal 0 is CTRL_C_EVENT)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stale_lock(root: Path | None = None) -> dict | None:
    """Return lock info when the lock is older than 120 s and its process is gone."""
    path = flamin_dir(root) / ".lock"
    if not path.exists():
        return None
    info = read_lock_info(path)
    age = time.time() - path.stat().st_mtime
    if age > STALE_SECONDS and not pid_alive(int(info.get("pid", 0) or 0)):
        info["age_seconds"] = int(age)
        return info
    return None


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(3)}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    for attempt in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: a reader holds the file open for a moment
            time.sleep(0.02)
    os.replace(tmp, path)


def sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


class Store:
    """The only writer of `.flamin/*.json`."""

    def __init__(self, root: Path | None = None):
        self.root = root or project_root()
        self.dir = flamin_dir(self.root)

    def exists(self) -> bool:
        return (self.dir / "state.json").exists()

    def path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    def load(self, name: str, default=None):
        p = self.path(name)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            if default is not None:
                return default
            raise FlaminError(f"No product state here ({p.name} missing). Run `flamin init` first.")
        except ValueError as exc:
            raise FlaminError(f".flamin/{p.name} is not valid JSON ({exc}). Run `flamin doctor`.")

    def save(self, name: str, data) -> None:
        text = canonical_json(data).encode("utf-8")
        with StateLock(self.root):
            atomic_write_bytes(self.path(name), text)
            atomic_write_bytes(sidecar(self.path(name)), (sha256_bytes(text) + "\n").encode())

    @contextlib.contextmanager
    def transaction(self):
        """Hold the state lock for a read-modify-write."""
        with StateLock(self.root):
            yield self

    def checksum_problems(self) -> list[str]:
        problems = []
        for name in STATE_FILES:
            p = self.path(name)
            if not p.exists():
                continue
            side = sidecar(p)
            if not side.exists():
                problems.append(f".flamin/{p.name}: checksum sidecar missing")
                continue
            want = side.read_text(encoding="utf-8").strip()
            got = sha256_bytes(p.read_bytes().replace(b"\r\n", b"\n"))  # git may check files out with CRLF
            if want != got:
                problems.append(f".flamin/{p.name}: checksum mismatch (edited outside the engine)")
        return problems


def utc_stamp() -> str:
    return now().strftime("%Y%m%dT%H%M%SZ")
