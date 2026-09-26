"""Audit logging (DESIGN §15.2, P-26). JSON Lines, redacted, append-only, never dropped."""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

from .secrets_scan import redact_obj
from .statefile import StateLock, atomic_write_bytes, flamin_dir, lock_wait, session_id
from .util import iso, now, project_root

MAX_BYTES = 1024 ** 3
MAX_AGE_DAYS = 365
FIELDS = ("ts", "session", "tool", "agent", "action", "target", "decision", "reason", "approver", "version")


def audit_dir(root: Path | None = None) -> Path:
    return flamin_dir(root) / "audit"


def _current_version(root: Path) -> str | None:
    try:
        with open(flamin_dir(root) / "state.json", "r", encoding="utf-8") as fh:
            return f"v{json.load(fh).get('version', 1)}"
    except (OSError, ValueError):
        return None


def make_entry(action: str, target: str = "", decision: str = "allow", reason: str = "",
               tool: str = "cli", agent: str | None = None, approver: str | None = None,
               root: Path | None = None, **extra) -> dict:
    root = root or project_root()
    entry = {
        "ts": iso(), "session": session_id(), "tool": tool, "agent": agent,
        "action": action, "target": target, "decision": decision, "reason": reason,
        "approver": approver, "version": _current_version(root),
    }
    entry.update(extra)
    return entry


def append(entry: dict, root: Path | None = None, wait: float | None = None) -> str:
    """Append one line. Returns 'log' or 'spill'. Lines are never dropped."""
    root = root or project_root()
    d = audit_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    dedupe = entry.pop("dedupe", None)
    line = json.dumps(redact_obj(entry), ensure_ascii=False, sort_keys=False) + "\n"
    lock = StateLock(root)
    if not lock.try_acquire(lock_wait() if wait is None else wait):
        spill = d / f"spill-{entry.get('session') or session_id()}.jsonl"
        with open(spill, "a", encoding="utf-8") as fh:
            fh.write(line)
        return "spill"
    try:
        if dedupe and _seen(root, dedupe):
            return "duplicate"
        merge_spills(root)
        _append_line(d, entry.get("ts") or iso(), line)
        retention(root)
    finally:
        lock.release()
    return "log"


SEEN_MAX = 2000


def _seen(root: Path, key: str) -> bool:
    """Called with the lock held. True when this tool call was already logged (D-44). Keeps the last 2000 keys."""
    f = flamin_dir(root) / "tmp" / "calls.json"
    try:
        keys = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        keys = []
    if key in keys:
        return True
    keys = (keys + [key])[-SEEN_MAX:]
    f.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(f, json.dumps(keys).encode())
    return False


def _append_line(d: Path, ts: str, line: str) -> None:
    day = ts[:10] if len(ts) >= 10 else now().strftime("%Y-%m-%d")
    with open(d / f"{day}.jsonl", "a", encoding="utf-8") as fh:
        fh.write(line)


def merge_spills(root: Path) -> int:
    """Called with the lock held. Moves spill lines into the daily files."""
    d = audit_dir(root)
    merged = 0
    for spill in sorted(d.glob("spill-*.jsonl")):
        claim = spill.with_suffix(".merging")
        try:
            os.replace(spill, claim)
        except OSError:
            continue
        with open(claim, "r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    ts = json.loads(line).get("ts", "")
                except ValueError:
                    ts = ""
                _append_line(d, ts, line if line.endswith("\n") else line + "\n")
                merged += 1
        os.remove(claim)
    return merged


def retention(root: Path, max_bytes: int | None = None, max_age_days: int | None = None) -> list[str]:
    """Delete the oldest daily files over 1 GB total, and any file older than 1 year. Logged, not gated."""
    max_bytes = int(os.environ.get("FLAMIN_AUDIT_MAX_BYTES", max_bytes or MAX_BYTES))
    max_age_days = max_age_days or MAX_AGE_DAYS
    d = audit_dir(root)
    files = sorted(p for p in d.glob("????-??-??.jsonl"))
    today = now().date()
    deleted = []
    for p in list(files):
        try:
            day = _dt.date.fromisoformat(p.stem)
        except ValueError:
            continue
        if (today - day).days > max_age_days:
            p.unlink()
            files.remove(p)
            deleted.append(f"{p.name} (older than {max_age_days} days)")
    total = sum(p.stat().st_size for p in files)
    while total > max_bytes and len(files) > 1:
        oldest = files.pop(0)
        total -= oldest.stat().st_size
        oldest.unlink()
        deleted.append(f"{oldest.name} (total over {max_bytes} bytes)")
    for what in deleted:
        e = make_entry("retention-delete", target=f".flamin/audit/{what.split(' ')[0]}", reason=what,
                       tool="engine", root=root)
        _append_line(d, e["ts"], json.dumps(e) + "\n")
    return deleted


def read_all(root: Path | None = None, day: str | None = None) -> list[dict]:
    d = audit_dir(root or project_root())
    if not d.exists():
        return []
    files = [d / f"{day}.jsonl"] if day else sorted(d.glob("????-??-??.jsonl")) + sorted(d.glob("spill-*.jsonl"))
    out = []
    for p in files:
        if not p.exists():
            continue
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def readable(e: dict) -> str:
    hm = (e.get("ts") or "")[11:16]
    agent = e.get("agent") or "-"
    reason = f" ({e['reason']})" if e.get("reason") else ""
    appr = f" by {e['approver']}" if e.get("approver") else ""
    return (f"{hm} {e.get('tool', '-')} {agent} {str(e.get('decision', '')).upper()} "
            f"{e.get('action', '')} {e.get('target', '')}{reason}{appr}").rstrip()


def stats(entries: list[dict]) -> dict:
    """Steps per agent per task. A task is one agent in one session."""
    per = {}
    for e in entries:
        if e.get("tool") in ("cli", "engine") or not e.get("agent"):
            continue
        key = (e.get("session"), e.get("agent"))
        per[key] = per.get(key, 0) + 1
    by_agent: dict[str, list[int]] = {}
    for (_, agent), n in per.items():
        by_agent.setdefault(agent, []).append(n)
    return {a: {"tasks": len(v), "avg_steps": round(sum(v) / len(v), 1), "max_steps": max(v)}
            for a, v in sorted(by_agent.items())}
