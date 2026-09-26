"""`flamin hook <event> --tool <name>`: the single entry point for every tool hook (DESIGN §4, §15).

Reads the tool's JSON on stdin, runs the checks, prints the tool's native reply.
No business rule lives in an adapter; they all live here and in the modules this file calls.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import approvals as ap
from . import audit
from . import boundary as bd
from . import locks as lk
from . import patch as pt
from . import policy as pol
from .phase import AGENTS, WORKERS, allowed_agents, check_path_phase
from .product import Product
from .profiles import classify
from .secrets_scan import find_secrets, redact
from .statefile import atomic_write_bytes, flamin_dir
from .util import iso, project_root, read_text, rel, sha256_text

TOOLS = ("claude", "codex", "cursor")
HEARTBEAT_FRESH_SECONDS = 15


@dataclass
class Call:
    tool: str
    event: str                      # native event name
    kind: str = "other"             # write | delete | shell | spawn | mcp | read | other
    tool_name: str = ""
    path: str | None = None         # project-relative
    abs_path: str | None = None
    new: str | None = None          # full new content for writes
    have_content: bool = True
    command: str | None = None
    spawn: str | None = None
    agent: str | None = None
    agent_source: str = "hook"
    session: str = ""
    mode: str | None = None
    model: str | None = None
    raw: dict = field(default_factory=dict)
    extra_writes: list = field(default_factory=list)  # more (path, new) pairs, e.g. one patch with many files
    call_key: str = ""          # one key per tool call, whichever hook file sent it (D-44)


@dataclass
class Decision:
    decision: str = "allow"  # allow | deny | ask
    reason: str = ""
    message: str = ""        # reminder text for non-blocking events
    request: str | None = None


# ====================================================================== entry

def run(event: str, tool: str, stdin_text: str | None = None) -> int:
    root = project_root()
    raw_text = sys.stdin.read() if stdin_text is None else stdin_text
    try:
        payload = json.loads(raw_text) if raw_text.strip() else {}
    except ValueError:
        payload = {"_unparsed": raw_text[:2000]}
    if isinstance(payload, dict) and "cursor_version" in payload:
        tool = "cursor"  # Cursor also reads Claude Code hook files; answer in Cursor's own format
    native = str(payload.get("hook_event_name") or event)
    _heartbeat(root, tool)
    sid = str(payload.get("session_id") or payload.get("conversation_id") or "")
    if sid:
        os.environ["FLAMIN_SESSION"] = "s-" + sha256_text(sid)[:4]
    p = Product(root, tool=tool)
    try:
        call = normalize(tool, native, payload, p)
        dec = evaluate(p, call, event)
    except Exception as exc:  # noqa: BLE001 - fail closed on pre-events, loudly
        call = Call(tool=tool, event=native, raw=payload)
        pre = _is_pre(tool, native, event)
        dec = Decision("deny" if pre else "allow", f"flamin hook error: {type(exc).__name__}: {exc}")
        _safe_log(p, call, dec, action="hook-error")
    return reply(tool, native, event, call, dec)


def _is_pre(tool: str, native: str, event: str) -> bool:
    return native in ("PreToolUse", "preToolUse", "beforeShellExecution", "beforeMCPExecution", "subagentStart") \
        or event == "pretool"


def _heartbeat(root: Path, tool: str) -> None:
    try:
        d = flamin_dir(root) / "tmp"
        if not flamin_dir(root).exists():
            return
        d.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(d / f"heartbeat-{tool}", iso().encode())
    except OSError:
        pass


def heartbeats(root: Path) -> dict[str, float]:
    out = {}
    d = flamin_dir(root) / "tmp"
    for t in TOOLS:
        f = d / f"heartbeat-{t}"
        if f.exists():
            out[t] = time.time() - f.stat().st_mtime
    return out


# ====================================================================== normalize

def normalize(tool: str, native: str, payload: dict, p: Product) -> Call:
    c = Call(tool=tool, event=native, raw=payload, session=os.environ.get("FLAMIN_SESSION", ""))
    c.call_key = call_key(native, payload)
    c.mode = payload.get("permission_mode")
    c.model = payload.get("model") or payload.get("subagent_model")
    ti = payload.get("tool_input")
    if isinstance(ti, str):
        try:
            ti = json.loads(ti)
        except ValueError:
            ti = {"_raw": ti}
    ti = ti or {}
    name = str(payload.get("tool_name") or "")
    c.tool_name = name

    # agent identity (DESIGN §5.1)
    if tool in ("claude", "codex"):
        c.agent = payload.get("agent_type") or "orchestrator"
        if c.agent not in AGENTS:
            c.agent_source = "unidentified"
    else:
        c.agent, c.agent_source = None, "lease"

    if tool == "cursor" and native == "beforeShellExecution":
        c.kind, c.command = "shell", str(payload.get("command", ""))
    elif tool == "cursor" and native == "beforeMCPExecution":
        c.kind, c.command = "mcp", json.dumps(ti)
    elif native in ("subagentStart", "SubagentStart"):
        c.kind = "spawn"
        c.spawn = str(payload.get("subagent_type") or payload.get("agent_type") or "")
    elif native == "afterFileEdit":
        c.kind = "afteredit"
        _set_path(c, p, payload.get("file_path"))
    elif name in ("Bash", "PowerShell", "Shell", "shell", "unified_exec"):
        c.kind, c.command = "shell", str(ti.get("command", ""))
    elif name in ("Agent", "Task", "collaborationspawn_agent", "spawn_agent"):
        c.kind = "spawn"
        c.spawn = str(ti.get("subagent_type") or ti.get("agent_type") or ti.get("subagentType") or "")
    elif name == "apply_patch":
        c.kind = "write"
        _from_patch(c, p, str(ti.get("command") or ti.get("patch") or ti.get("input") or ""))
    elif name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        c.kind = "write"
        _set_path(c, p, ti.get("file_path") or ti.get("notebook_path") or ti.get("path"))
        c.new = _new_content(p, name, ti, c)
    elif name == "Delete":
        c.kind = "delete"
        _set_path(c, p, ti.get("file_path") or ti.get("path"))
    elif name.startswith("mcp__") or name.startswith("MCP:"):
        c.kind, c.command = "mcp", json.dumps(ti)
    elif name in ("Read", "Grep", "Glob", "LS", "WebSearch", "WebFetch", "TodoWrite", "ToolSearch",
                  "collaborationwait_agent", "view_image", "update_plan"):
        c.kind = "read"
    elif isinstance(ti, dict) and (ti.get("file_path") or ti.get("path")) and native in ("preToolUse", "PreToolUse"):
        # Unknown tool that carries a file path: treat as a write, never assume it is safe (P-15).
        c.kind = "write"
        _set_path(c, p, ti.get("file_path") or ti.get("path"))
        content = ti.get("content") or ti.get("contents") or ti.get("new_content")
        c.new, c.have_content = (content, True) if isinstance(content, str) else (None, False)
    if tool == "cursor" and c.agent is None:
        c.agent, c.agent_source = _lease_agent(p, c.path)
    return c


def call_key(native: str, payload: dict) -> str:
    """D-44: tool_use_id, else generation_id + hook event + a hash of the tool input."""
    tid = payload.get("tool_use_id")
    if tid:
        return f"{native}:{tid}"
    gid = payload.get("generation_id")
    if gid:
        ti = payload.get("tool_input") if payload.get("tool_input") is not None else payload.get("command", "")
        return f"{native}:{gid}:" + sha256_text(json.dumps(ti, sort_keys=True, default=str))[:16]
    return ""


def _set_path(c: Call, p: Product, path) -> None:
    if not path:
        return
    c.abs_path = str(path)
    c.path = rel(path, p.root)


def _new_content(p: Product, name: str, ti: dict, c: Call) -> str | None:
    if name == "Write":
        return ti.get("content")
    current = read_text(p.root / c.path) if c.path else None
    if name == "Edit":
        if current is None:
            c.have_content = False
            return None
        old_s, new_s = ti.get("old_string", ""), ti.get("new_string", "")
        cur = current.replace("\r\n", "\n")
        old_s, new_s = old_s.replace("\r\n", "\n"), new_s.replace("\r\n", "\n")
        if old_s not in cur:
            c.have_content = False
            return None
        return cur.replace(old_s, new_s) if ti.get("replace_all") else cur.replace(old_s, new_s, 1)
    if name == "MultiEdit":
        cur = (current or "").replace("\r\n", "\n")
        for e in ti.get("edits", []):
            o, n = e.get("old_string", ""), e.get("new_string", "")
            if o not in cur:
                c.have_content = False
                return None
            cur = cur.replace(o, n) if e.get("replace_all") else cur.replace(o, n, 1)
        return cur
    c.have_content = False
    return None


def _from_patch(c: Call, p: Product, text: str) -> None:
    try:
        ops = pt.parse(text)
    except pt.PatchError:
        c.have_content = False
        return
    writes = []
    for op in ops:
        path = rel(op["path"], p.root)
        if op["op"] == "add":
            writes.append((path, "\n".join(op["lines"]) + ("\n" if op["lines"] else ""), True))
        elif op["op"] == "delete":
            writes.append((path, None, True))
        else:
            cur = read_text(p.root / path)
            try:
                new = pt.apply_update(cur or "", op["hunks"])
                ok = cur is not None
            except pt.PatchError:
                new, ok = None, False
            if op.get("move_to"):
                writes.append((path, None, True))
                writes.append((rel(op["move_to"], p.root), new, ok))
            else:
                writes.append((path, new, ok) if ok else (path, None, False))
    if writes:
        first = writes[0]
        c.path, c.new, c.have_content = first[0], first[1], first[2]
        if first[1] is None and first[2]:
            c.kind = "delete"
        c.extra_writes = writes[1:]


def _lease_agent(p: Product, path: str | None) -> tuple[str | None, str]:
    """Cursor: agent identity from the module lease (Heuristic)."""
    if not path or not p.store.exists():
        return None, "unknown"
    try:
        state = p.store.load("state")
    except Exception:  # noqa: BLE001
        return None, "unknown"
    info = classify(path, p.profiles())
    if info.module:
        lease = state.get("modules", {}).get(info.module, {}).get("lease")
        if lease:
            return lease.split("@")[0], "lease"
    return None, "unknown"


# ====================================================================== evaluate

def evaluate(p: Product, c: Call, event: str) -> Decision:
    ev = c.event
    if ev in ("PostToolUse", "postToolUse", "afterShellExecution", "afterMCPExecution", "postToolUseFailure"):
        return post_tool(p, c)
    if ev == "afterFileEdit":
        return after_file_edit(p, c)
    if ev in ("Stop", "stop", "SessionEnd", "sessionEnd", "SubagentStop", "subagentStop") or event == "sessionend":
        return session_end(p, c)
    if ev in ("PreCompact", "preCompact") or event == "precompact":
        return Decision("allow", message="flamin: context is being compacted. The Orchestrator writes a handoff "
                        "first (`flamin handoff --validate <file>`): Objective, State Ledger, Artifact References.")
    if ev == "SubagentStart" and c.tool in ("codex", "claude"):
        d = check_spawn(p, c, second_check=True)
        _safe_log(p, c, d, action="spawn-check")
        return d
    d = pre_tool(p, c)
    _safe_log(p, c, d)
    return d


def _state(p: Product) -> dict | None:
    if not p.store.exists():
        return None
    return p.store.load("state")


def pre_tool(p: Product, c: Call) -> Decision:
    if c.kind == "spawn":
        return check_spawn(p, c)
    if c.kind == "shell":
        return check_shell(p, c)
    if c.kind == "mcp":
        spans = find_secrets(c.command or "", include_passwords=False)
        if spans:
            return Decision("deny", f"Secret leak blocked: the call arguments contain a {spans[0][0]}.")
        return Decision("allow", "mcp call")
    if c.kind in ("write", "delete"):
        writes = [(c.path, c.new if c.kind == "write" else None, c.have_content, c.kind == "delete")]
        writes += [(pth, new, ok, new is None and ok) for pth, new, ok in c.extra_writes]
        for pth, new, ok, is_delete in writes:
            d = check_write(p, c, pth, new, ok, is_delete)
            if d.decision != "allow":
                return d
        return Decision("allow", "write checks passed")
    return Decision("allow", "")


# ---------------------------------------------------------------- writes

def check_write(p: Product, c: Call, relpath: str | None, new: str | None, have_content: bool,
                is_delete: bool) -> Decision:
    if not relpath:
        return Decision("allow", "no path")
    if not _inside(relpath):
        return Decision("allow", "outside the project")
    if pol.is_state_path(relpath):
        return Decision("deny", f"{relpath} is engine state. Only the flamin engine writes it; use a `flamin <verb>` command.")
    if pol.is_enforcement_path(relpath) and not (pol.maintenance_mode(p.root) and pol.is_kit_file(relpath)):
        return Decision("deny", f"{relpath} is part of the enforcement layer (kit, launchers, tool config, rules files, "
                        ".git, .flamin/stacks). Agents may not change it; only `flamin init`, `flamin upgrade` or an "
                        "approved stack profile change write it.")
    state = _state(p)
    profiles = p.profiles() if state else []
    info = classify(relpath, profiles)
    if new and not is_delete:
        spans = find_secrets(new, include_passwords=not info.is_test)
        if spans:
            return Decision("deny", f"Secret leak blocked: the new content of {relpath} contains a {spans[0][0]}. "
                            "Keep secrets in environment variables or a secrets store, never in files.")
    if state is None:
        return Decision("allow", "no product state")
    # per-agent path rules (Hard on Claude Code and Codex, Soft on Cursor)
    if c.agent and c.agent_source == "hook":
        ok, why = pol.agent_may_write(c.agent, relpath, info)
        if not ok:
            return Decision("deny", why)
    elif c.agent_source == "unidentified":
        pass  # an unknown agent type: caller-agnostic rules below still apply
    # locks
    old = read_text(p.root / relpath)
    entry = p.locks().get("files", {}).get(relpath)
    if entry or lk.level_of(old):
        if not have_content and not is_delete:
            return Decision("deny", f"{relpath} is locked and this tool call's full new content cannot be read, "
                            "so it cannot be checked. Use a Write or Edit with the full text.")
        ok, why = lk.check_lock(relpath, old, None if is_delete else new, entry)
        if not ok:
            return Decision("deny", why)
    # boundaries
    if new and not is_delete:
        v = bd.check_boundary(relpath, new, profiles, p.modules())
        if v:
            return Decision("deny", f"Module boundary: {relpath}: " + "; ".join(v))
    # phase and step order
    ok, why = check_path_phase(info, relpath, state, mode="write")
    if not ok:
        return Decision("deny", "Phase order: " + why)
    # Approval Gates
    if is_delete:
        return gate(p, c, "delete", f"delete {relpath}", f"delete file {relpath}")
    if pol.is_secret_file(relpath):
        return gate(p, c, "secret-change", f"write {relpath} (sha256 {sha256_text(new or '')[:16]})",
                    f"change secret or credential file {relpath}")
    if pol.is_dependency_file(relpath) and new is not None and _deps_changed(relpath, old, new):
        return gate(p, c, "dependency", f"write {relpath} (sha256 {sha256_text(new)[:16]})",
                    f"change dependencies in {relpath}")
    return Decision("allow", "write checks passed")


def _inside(relpath: str) -> bool:
    return not (relpath.startswith("..") or re.match(r"^[A-Za-z]:", relpath) or relpath.startswith("/"))


def _deps_changed(relpath: str, old: str | None, new: str) -> bool:
    return _deps(relpath, old) != _deps(relpath, new)


def _deps(relpath: str, text: str | None) -> frozenset:
    if not text:
        return frozenset()
    name = relpath.rsplit("/", 1)[-1]
    try:
        if name == "package.json":
            d = json.loads(text)
            return frozenset(f"{k}:{n}@{v}" for k in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
                             for n, v in (d.get(k) or {}).items())
        if name == "pyproject.toml":
            d = tomllib.loads(text)
            deps = list(d.get("project", {}).get("dependencies", []))
            for g, lst in d.get("project", {}).get("optional-dependencies", {}).items():
                deps += [f"{g}:{x}" for x in lst]
            deps += [f"poetry:{k}" for k in d.get("tool", {}).get("poetry", {}).get("dependencies", {})]
            return frozenset(deps)
        if name.startswith("requirements"):
            return frozenset(l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#"))
        if name == "pom.xml":
            return frozenset(re.findall(r"<artifactId>([^<]+)</artifactId>\s*(?:<version>([^<]+)</version>)?", text))
    except (ValueError, tomllib.TOMLDecodeError):
        return frozenset({text})
    return frozenset(re.findall(r"^\s*[\w.\-:@/\"']+.*$", text, re.MULTILINE))


# ---------------------------------------------------------------- shell

def check_shell(p: Product, c: Call) -> Decision:
    cmd = c.command or ""
    if c.agent and c.agent_source == "hook" and c.agent in WORKERS and not pol.agent_has_shell(c.agent):
        return Decision("deny", f"The {c.agent} agent has no shell tool (D-41). Every shell call from it is denied; "
                        "report back to the Orchestrator instead.")
    what = pol.human_only(cmd)
    if what:
        args = pol.normalize_flamin_args(cmd) or ""
        if _can_ask(c):
            ap.write_ask_token(p.root, args, c.tool)
            return Decision("ask", f"flamin: `{args}` is a human-only decision ({what}). Confirm only if you, the human, "
                            "agree with exactly this command.")
        return Decision("deny", f"`{args or cmd}` is a human-only command ({what}). Agents never answer a gate. "
                        f"The human runs it in a terminal: flamin {args}" + ("" if re.search(r"--(yes|no)\b", args) or "clear-stale" in args or "upgrade" in args else " --yes   (or --no)"))
    why = pol.destructive(cmd)
    if why:
        return Decision("deny", f"Destructive command blocked ({why}).")
    spans = find_secrets(cmd, include_passwords=True)
    if spans:
        return Decision("deny", f"Secret leak blocked: the command line contains a {spans[0][0]}. Use an environment variable.")
    state = _state(p)
    locks = p.locks().get("files", {}) if state else {}
    for target in pol.shell_write_targets(cmd):
        rp = rel(target, p.root)
        if not _inside(rp):
            continue
        if pol.is_state_path(rp) or (pol.is_enforcement_path(rp)
                                     and not (pol.maintenance_mode(p.root) and pol.is_kit_file(rp))):
            return Decision("deny", f"Shell write to {rp} blocked: engine state and the enforcement layer are "
                            "written only by flamin itself.")
        if rp in locks or lk.level_of(read_text(p.root / rp)):
            return Decision("deny", f"Shell write to locked file {rp} blocked. Only `flamin generate` changes locked "
                            "code; hand-written logic goes in extension points (use Edit or Write).")
    kind = pol.gated_shell(cmd)
    if kind:
        return gate(p, c, kind, cmd, f"run `{cmd[:160]}`")
    return Decision("allow", "shell checks passed")


def _can_ask(c: Call) -> bool:
    if c.tool == "claude" and c.event == "PreToolUse":
        return c.mode in ap.INTERACTIVE_MODES["claude"]
    if c.tool == "cursor" and c.event in ("beforeShellExecution", "beforeMCPExecution"):
        return bool(c.mode) and c.mode in ap.INTERACTIVE_MODES["cursor"]
    return False


# ---------------------------------------------------------------- gates

def gate(p: Product, c: Call, kind: str, payload: str, title: str) -> Decision:
    if not p.store.exists():
        return Decision("deny", f"{title}: this is an Approval Gate item and there is no product state to record an "
                        "approval. Run `flamin init` first.")
    full_payload = f"{kind}\n{payload}"
    with p.tx() as (state, appr):
        if c.call_key:  # the same tool call arriving twice (D-44) keeps the answer it already got
            for req in appr["requests"].values():
                if req.get("consumed_key") == c.call_key and req["payload_hash"] == sha256_text(full_payload):
                    return Decision("allow", f"approved by {req['approver']} ({req['id']})", request=req["id"])
        done = ap.approved_unused(appr, "action", full_payload)
        if done:
            done["consumed"] = iso()
            done["consumed_key"] = c.call_key
            return Decision("allow", f"approved by {done['approver']} ({done['id']})", request=done["id"])
        asking = _can_ask(c)
        req = ap.open_request(p.store, state, appr, "action", title, full_payload,
                              [f"1. Understood: an agent wants to {title}",
                               f"2. Planned:    {c.tool} tool call ({c.tool_name or c.event}) by {c.agent or 'unknown agent'}",
                               f"3. Because:    '{kind}' is an Approval Gate item (DESIGN §15.3)"],
                              data={"gate": kind, "tool": c.tool}, status="asked" if asking else "pending")
    if asking:
        return Decision("ask", f"flamin gate {req['id']}: {title}. Approve only if you agree with exactly this.",
                        request=req["id"])
    return Decision("deny", f"Approval needed ({req['id']}): {title}. This tool cannot ask you safely in this mode, so "
                    f"the human runs `flamin approve {req['id']} --yes` (or --no) in a terminal, then asks the agent "
                    "to retry the exact same action. No answer means no.", request=req["id"])


# ---------------------------------------------------------------- spawns

def check_spawn(p: Product, c: Call, second_check: bool = False) -> Decision:
    name = (c.spawn or "").strip().lower()
    if name == "orchestrator":
        return Decision("deny", "The Orchestrator is the main session. It is never started as a sub-agent.")
    if name not in WORKERS:
        return Decision("deny", f"Only the seven flamin agents may be started ({', '.join(WORKERS)}); "
                        f"'{c.spawn}' is not one of them.")
    raw = c.raw
    if c.tool == "claude" and raw.get("agent_id") and not second_check:
        return Decision("deny", "Delegation depth is 1: only the Orchestrator starts agents.")
    if c.tool == "codex" and raw.get("agent_type") and not second_check:
        return Decision("deny", "Delegation depth is 1: only the Orchestrator starts agents.")
    if c.tool == "cursor":
        known = _cursor_subagents(p)
        if str(raw.get("parent_conversation_id") or "") in known:
            return Decision("deny", "Delegation depth is 1: a sub-agent may not start another agent (Heuristic check).")
    state = _state(p)
    if state is not None:
        allowed = allowed_agents(state)
        if name not in allowed:
            return Decision("deny", f"Phase gate: '{name}' may not start now. Allowed at this step: "
                            f"{', '.join(sorted(allowed)) or 'none (human decision pending)'}.")
    msg = _model_warning(p, c, name)
    if c.tool == "cursor" and raw.get("subagent_id"):
        _remember_cursor_subagent(p, str(raw.get("subagent_id")))
    return Decision("allow", "launch allowed", message=msg)


def _model_warning(p: Product, c: Call, name: str) -> str:
    real = c.raw.get("subagent_model") if c.tool == "cursor" else (c.raw.get("model") if c.event == "SubagentStart" else None)
    if not real:
        return ""
    spec = pol.agent_defs().get(name, {})
    models = p.store.load("models", default={}) if p.store.exists() else {}
    want = models.get(c.tool, {}).get(spec.get("tier", ""))
    if want and spec.get("tier") == "Deep" and want not in str(real):
        return f"flamin WARNING: '{name}' is a Deep-tier agent but runs on '{real}', not '{want}'."
    return ""


def _cursor_subagents(p: Product) -> set[str]:
    f = p.fdir / "tmp" / "cursor-subagents.json"
    try:
        return set(json.loads(f.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _remember_cursor_subagent(p: Product, sid: str) -> None:
    known = _cursor_subagents(p)
    known.add(sid)
    atomic_write_bytes(p.tmp() / "cursor-subagents.json", json.dumps(sorted(known)).encode())


# ---------------------------------------------------------------- post events

def post_tool(p: Product, c: Call) -> Decision:
    # A call that ran after an "ask" means the human confirmed the tool's own prompt.
    if p.store.exists() and c.kind in ("shell", "write", "delete"):
        with p.tx() as (state, appr):
            for req in appr["requests"].values():
                if req["status"] == "asked" and req["data"].get("tool") == c.tool and _matches(req, c):
                    req.update({"status": "approved", "consumed": iso(), "answered": iso(),
                                "approver": f"human via {c.tool} prompt"})
                    ap.sync_open_list(state, appr)
                    p.log("gate-answer", target=req["id"], decision="allow", reason=req["title"],
                          approver=req["approver"])
    _safe_log(p, c, Decision("allow", "done"), action=f"done:{c.kind}")
    return Decision("allow")


def _matches(req: dict, c: Call) -> bool:
    payload = req["payload"].split("\n", 1)[-1]
    if c.kind == "shell":
        return payload == (c.command or "")
    return c.path is not None and payload.split(" (sha256")[0].endswith(c.path)


def after_file_edit(p: Product, c: Call) -> Decision:
    """Cursor backstop (P-10/D-21): reverse a lock or boundary breach made by any edit tool."""
    if not c.path or not p.store.exists():
        return Decision("allow")
    current = read_text(p.root / c.path)
    if current is None:
        return Decision("allow")
    old = current.replace("\r\n", "\n")
    for e in reversed(c.raw.get("edits") or []):
        o, n = (e.get("old_string") or "").replace("\r\n", "\n"), (e.get("new_string") or "").replace("\r\n", "\n")
        if n and n in old:
            old = old.replace(n, o, 1)
        elif not n and o:
            old = None
            break
    reasons = []
    entry = p.locks().get("files", {}).get(c.path)
    if (entry or lk.level_of(old)) and old is not None:
        ok, why = lk.check_lock(c.path, old, current, entry)
        if not ok:
            reasons.append(why)
    elif entry and old is None:
        ok, why = lk.check_lock(c.path, None, current, entry)
        if not ok:
            reasons.append(why)
    v = bd.check_boundary(c.path, current, p.profiles(), p.modules())
    if v:
        reasons.append("Module boundary: " + "; ".join(v))
    if not reasons:
        _safe_log(p, c, Decision("allow", "post-edit check passed"), action="afteredit")
        return Decision("allow")
    if old is not None:
        atomic_write_bytes(p.root / c.path, old.encode("utf-8"))
        d = Decision("deny", "Edit reversed: " + " | ".join(reasons))
    else:
        d = Decision("deny", "Breach detected but the old text could not be rebuilt; restore it with git: " + " | ".join(reasons))
    _safe_log(p, c, d, action="auto-revert")
    return d


def session_end(p: Product, c: Call) -> Decision:
    """Docs-as-memory reminder: a reminder, never a block (DESIGN §17)."""
    if not p.store.exists():
        return Decision("allow")
    sess = os.environ.get("FLAMIN_SESSION", "")
    code, docs = False, False
    for e in audit.read_all(p.root):
        if e.get("session") != sess or not str(e.get("action", "")).startswith("done:"):
            continue
        t = str(e.get("target") or "")
        if t.startswith(".flamin/"):
            docs = True
        elif t and not t.startswith(".git"):
            code = True
    _safe_log(p, c, Decision("allow", "session end"), action="session-end")
    if code and not docs:
        return Decision("allow", message="flamin reminder: product code changed in this session but nothing under "
                        ".flamin/ did. Update the docs that remember this work (business rules, technical notes, "
                        "a handoff) before you stop.")
    return Decision("allow")


# ====================================================================== logging and replies

def _safe_log(p: Product, c: Call, d: Decision, action: str | None = None) -> None:
    try:
        if not p.store.exists():
            return
        target = c.path or c.spawn or (c.command or "")[:300]
        extra = {"agent_source": c.agent_source, "tool_name": c.tool_name, "event": c.event}
        if c.mode:
            extra["permission_mode"] = c.mode
        if d.request:
            extra["request"] = d.request
        if c.call_key:
            extra["dedupe"] = f"{c.call_key}:{action or c.kind}"  # one audit line per tool call (D-44)
        p.agent = c.agent
        p.log(action or c.kind, target=target, decision=d.decision, reason=d.reason, **extra)
    except Exception:  # noqa: BLE001 - logging must never break a hook
        pass


def reply(tool: str, native: str, event: str, c: Call, d: Decision) -> int:
    reason = redact(d.reason or "")
    msg = redact(d.message or "")
    if tool == "claude":
        if native == "PreToolUse" or (event == "pretool" and native not in ("SubagentStart",)):
            if d.decision == "deny":
                _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}})
                sys.stderr.write(reason + "\n")
                return 2
            if d.decision == "ask":
                _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                              "permissionDecisionReason": reason}})
                return 0
            if msg:
                _emit({"systemMessage": msg})
            return 0  # plain allow: no output, so Claude Code's own permission rules still apply
        if native == "SubagentStart" and d.decision == "deny":
            _emit({"hookSpecificOutput": {"hookEventName": "SubagentStart",
                                          "additionalContext": "flamin: STOP. " + reason}})
            return 0
        if native in ("SessionEnd",):
            return 0
        if msg or d.decision == "deny":
            _emit({"systemMessage": msg or reason})
        return 0
    if tool == "codex":
        if native == "PreToolUse" or (event == "pretool" and native not in ("SubagentStart",)):
            if d.decision in ("deny", "ask"):  # Codex has no "ask" in PreToolUse: always deny
                # JSON deny with exit 0. Probed: on Windows Codex runs hooks through PowerShell, which turns
                # exit code 2 into 1, and Codex then treats the hook as failed and lets the call continue.
                _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}})
                sys.stderr.write(reason + "\n")
                return 0
            return 0
        if native == "SubagentStart":
            if d.decision == "deny":
                _emit({"systemMessage": reason, "hookSpecificOutput": {
                    "hookEventName": "SubagentStart", "additionalContext": "flamin: STOP and return at once. " + reason}})
            elif msg:
                _emit({"systemMessage": msg})
            return 0
        if native == "Stop":
            _emit({"systemMessage": msg} if msg else {})
            return 0
        if native == "SessionEnd":
            return 0
        if msg:
            _emit({"systemMessage": msg})
        return 0
    # cursor
    if native in ("preToolUse", "beforeShellExecution", "beforeMCPExecution", "subagentStart"):
        dec = d.decision
        if dec == "ask" and native in ("preToolUse", "subagentStart"):
            dec = "deny"
        out = {"permission": dec}
        if dec != "allow":
            out["user_message"] = reason
            out["agent_message"] = reason
        elif msg:
            out["user_message"] = msg
        _emit(out)
        return 0  # the JSON decides; PowerShell on Windows would not pass exit code 2 through unchanged
    if native == "preCompact":
        _emit({"user_message": msg} if msg else {})
        return 0
    if native == "afterFileEdit" and d.decision == "deny":
        sys.stderr.write(reason + "\n")
    _emit({})
    return 0


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()
