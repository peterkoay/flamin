"""Approval Gates and assumption status (DESIGN §7.2, §15.3, P-20/D-31, P-21/D-32).

All gate decisions live only in `.flamin/approvals.json`. Markdown notes are never read for a decision.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .statefile import Store, atomic_write_bytes, flamin_dir
from .util import FlaminError, iso, read_text, sha256_text

ASK_TOKEN_SECONDS = 600
ASSUMPTION_RX = re.compile(r"^#{2,3}\s+(A-\d{3,})\b[:\s]*(.*)$", re.MULTILINE)
SCOPE_RX = re.compile(r"^\s*[-*]\s*Scope\s*:\s*([\w\-/ ,]+)", re.IGNORECASE | re.MULTILINE)

# Interactive permission modes where the tool's own prompt reaches the human (D-32).
INTERACTIVE_MODES = {
    # "auto" is left out on purpose: D-32 denies in any auto-approve mode, even though the Claude Code docs say a
    # hook's "ask" still forces a prompt there. Adding it is a review-round decision, not a build choice.
    "claude": {"default", "acceptEdits", "plan"},
    "codex": set(),   # "ask" is not supported in Codex PreToolUse
    "cursor": set(),  # no permission-mode field in Cursor hook input, so the mode is unknown
}


def empty() -> dict:
    return {"requests": {}, "assumptions": {}}


def load(store: Store) -> dict:
    return store.load("approvals", default=empty())


def next_request_id(state: dict) -> str:
    state["counters"]["request"] = state["counters"].get("request", 0) + 1
    return f"R-{state['counters']['request']:04d}"


def open_request(store: Store, state: dict, appr: dict, kind: str, title: str, payload: str,
                 preview: list[str], data: dict | None = None, status: str = "pending") -> dict:
    """Create a request, or return the existing open one for the same exact payload. Caller saves."""
    h = sha256_text(payload)
    for req in appr["requests"].values():
        if req["kind"] == kind and req["payload_hash"] == h and req["status"] in ("pending", "asked"):
            return req
    rid = next_request_id(state)
    req = {
        "id": rid, "kind": kind, "title": title, "preview": preview, "payload": payload,
        "payload_hash": h, "status": status, "created": iso(), "answered": None,
        "approver": None, "data": data or {},
    }
    appr["requests"][rid] = req
    sync_open_list(state, appr)
    return req


def sync_open_list(state: dict, appr: dict) -> None:
    state["open_requests"] = sorted(r["id"] for r in appr["requests"].values() if r["status"] in ("pending", "asked"))


def approved_unused(appr: dict, kind: str, payload: str) -> dict | None:
    h = sha256_text(payload)
    for req in appr["requests"].values():
        if req["kind"] == kind and req["payload_hash"] == h and req["status"] == "approved" and not req.get("consumed"):
            return req
    return None


def format_request(req: dict) -> str:
    lines = [f"Approval needed: {req['id']} — {req['title']}"]
    lines += [f"  {p}" for p in req.get("preview", [])]
    payload = req.get("payload", "")
    if payload:
        shown = payload if len(payload) < 3000 else payload[:3000] + "\n  ... (truncated in this view; the hash covers all of it)"
        lines.append("  Exact change:")
        lines += ["    " + ln for ln in shown.splitlines()]
    lines.append(f"  Answer in a terminal: flamin approve {req['id']} --yes   (or --no)")
    return "\n".join(lines)


# ---------------------------------------------------------------- ask tokens

def _token_dir(root: Path) -> Path:
    return flamin_dir(root) / "tmp" / "ask"


def token_key(args: str) -> str:
    return sha256_text(args.strip())[:32]


def write_ask_token(root: Path, args: str, tool: str) -> None:
    d = _token_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(d / token_key(args), json.dumps({"args": args, "tool": tool, "at": time.time()}).encode())


def take_ask_token(root: Path, args: str) -> str | None:
    """Return the tool whose prompt the human confirmed, and consume the token."""
    p = _token_dir(root) / token_key(args)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        p.unlink()
    except OSError:
        pass
    if time.time() - float(data.get("at", 0)) > ASK_TOKEN_SECONDS:
        return None
    return data.get("tool")


# ---------------------------------------------------------------- assumptions

def parse_assumptions(root: Path) -> dict[str, dict]:
    text = read_text(flamin_dir(root) / "decisions" / "assumptions.md") or ""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)  # examples in comments are not assumptions
    out = {}
    matches = list(ASSUMPTION_RX.finditer(text))
    for i, m in enumerate(matches):
        block = text[m.start(): matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        sm = SCOPE_RX.search(block)
        scopes = [s.strip() for s in (sm.group(1) if sm else "product").split(",") if s.strip()]
        out[m.group(1)] = {"title": m.group(2).strip(), "scope": scopes, "text": block.strip()}
    return out


def sync_assumptions(root: Path, appr: dict) -> bool:
    """Register every assumption id ever seen as open. Removing it from the notes never closes it."""
    changed = False
    for aid, a in parse_assumptions(root).items():
        rec = appr["assumptions"].get(aid)
        if rec is None:
            appr["assumptions"][aid] = {"status": "open", "scope": a["scope"], "title": a["title"],
                                        "text_hash": sha256_text(a["text"]), "seen": iso()}
            changed = True
        elif rec["status"] == "open" and rec.get("text_hash") != sha256_text(a["text"]):
            rec["text_hash"] = sha256_text(a["text"])
            rec["scope"] = a["scope"]
            changed = True
    return changed


def open_assumptions(appr: dict, scope: str | None = None) -> list[str]:
    out = []
    for aid, rec in sorted(appr["assumptions"].items()):
        if rec["status"] != "open":
            continue
        scopes = rec.get("scope") or ["product"]
        if scope is None or "product" in scopes or scope in scopes:
            out.append(aid)
    return out


def require_no_open_assumptions(root: Path, store: Store, state: dict, scope: str | None, what: str) -> None:
    appr = load(store)
    if sync_assumptions(root, appr):
        store.save("approvals", appr)
    open_ids = open_assumptions(appr, scope)
    if open_ids:
        raise FlaminError(
            f"{what} refused: assumption(s) still open: {', '.join(open_ids)}. "
            "Each needs an explicit \"agreed\" from the human, recorded with `flamin approve <id> --yes` "
            "(silence is not agreement)."
        )
