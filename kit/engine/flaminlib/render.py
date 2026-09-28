"""Adapters: render the tool-neutral core into each tool's native files (DESIGN §4, D-24, D-36).

No business rule lives here: every hook calls `flamin hook <event> --tool <name>`.
Shared files are the same on every OS. Machine-local files name a Python command and are rendered
for the current OS only (D-15).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .util import KIT_DIR, read_text

ADAPTERS = KIT_DIR / "adapters"
WORKERS = ("business", "analyst", "architect", "planner", "designer", "developer", "tester")
CLAUDE_TOOLS = {"read": ["Read"], "search": ["Grep", "Glob"], "write": ["Write"], "edit": ["Edit"],
                "shell": ["Bash", "PowerShell"]}
CLAUDE_BUILTINS_DENY = ["Agent(fork)", "Agent(Explore)", "Agent(Plan)", "Agent(general-purpose)", "Agent(claude)",
                        "Agent(statusline-setup)", "Agent(claude-code-guide)"]


def _agent(name: str) -> dict:
    return json.loads((KIT_DIR / "agents" / f"{name}.json").read_text(encoding="utf-8"))


def _prompt(name: str) -> str:
    return (read_text(KIT_DIR / "agents" / f"{name}.md") or "").replace("\r\n", "\n").rstrip() + "\n"


def _models() -> dict:
    return json.loads((KIT_DIR / "agents" / "models.json").read_text(encoding="utf-8"))


def _core() -> str:
    return (read_text(KIT_DIR / "rules" / "core.md") or "").replace("\r\n", "\n").rstrip() + "\n"


def _header(tool: str, name: str) -> str:
    return (read_text(ADAPTERS / tool / name) or "").replace("\r\n", "\n")


def _yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _toml_multiline(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return '"""\n' + s + '"""'


# ====================================================================== shared files

def claude_shared() -> dict[str, str]:
    models = _models()["claude"]
    files = {"CLAUDE.md": _header("claude", "CLAUDE.header.md") + "\n" + _core()}
    settings = {
        "agent": "orchestrator",
        "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1", "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "10"},
        "permissions": {"deny": CLAUDE_BUILTINS_DENY},
    }
    files[".claude/settings.json"] = json.dumps(settings, indent=2) + "\n"
    for name in ("orchestrator",) + WORKERS:
        a = _agent(name)
        tools = []
        for t in a["tools"]:
            if t == "delegate":
                tools.append("Agent(" + ", ".join(WORKERS) + ")")
            else:
                tools += CLAUDE_TOOLS[t]
        fm = [
            "---",
            f"name: {name}",
            f"description: {_yaml_str(a['description'])}",
            f"tools: {', '.join(tools)}",
            f"model: {models[a['tier']]}",
            "---",
        ]
        files[f".claude/agents/{name}.md"] = "\n".join(fm) + "\n\n" + _prompt(name)
    return files


def codex_shared() -> dict[str, str]:
    models = _models()["codex"]
    files = {}
    files[".codex/config.toml"] = _header("codex", "config.toml") + "\n" + codex_hooks_toml()
    effort = {"Deep": "high", "Balanced": "medium", "Fast": "low"}
    for name in WORKERS:
        a = _agent(name)
        sandbox = "read-only" if a.get("read_only") else "workspace-write"
        body = [
            f"name = {json.dumps(name)}",
            f"description = {json.dumps(a['description'], ensure_ascii=False)}",
            f"model = {json.dumps(models[a['tier']])}",
            f"model_reasoning_effort = {json.dumps(effort[a['tier']])}",
            f"sandbox_mode = {json.dumps(sandbox)}",
            f"developer_instructions = {_toml_multiline(_prompt(name))}",
        ]
        files[f".codex/agents/{name}.toml"] = "# Rendered by flamin init from kit/agents/. Do not edit.\n" + "\n".join(body) + "\n"
    return files


CODEX_TOOLS = "^(Bash|apply_patch|collaborationspawn_agent|mcp__.*)$"


def codex_hook_groups() -> list[tuple[str, str | None, str]]:
    """(event, matcher, engine event) for every Codex hook."""
    return [("PreToolUse", CODEX_TOOLS, "pretool"), ("SubagentStart", None, "pretool"),
            ("PostToolUse", CODEX_TOOLS, "posttool"), ("Stop", None, "sessionend"), ("SessionEnd", None, "sessionend")]


def codex_commands(event: str) -> tuple[str, str]:
    # The project root comes from git, as the Codex docs advise. On Windows Codex runs command_windows through
    # PowerShell (probed): the launcher needs a path, and `exit $LASTEXITCODE` passes the exit code through.
    posix = f'"$(git rev-parse --show-toplevel)/flamin" hook {event} --tool codex'
    windows = f'& "$(git rev-parse --show-toplevel)\\flamin.cmd" hook {event} --tool codex; exit $LASTEXITCODE'
    return posix, windows


def codex_hooks_toml() -> str:
    """Hooks inline in .codex/config.toml (DESIGN §4.2 allows inline or hooks.json). TOML key command_windows
    is documented and was probed on Codex 0.153.4; the JSON spelling for hooks.json was not provable here."""
    lines = ["# Hooks: every hook calls the flamin engine. Codex skips changed hooks until you trust them again in /hooks."]
    for ev, matcher, engine_ev in codex_hook_groups():
        posix, windows = codex_commands(engine_ev)
        lines.append(f"\n[[hooks.{ev}]]")
        if matcher:
            lines.append(f"matcher = '{matcher}'")
        timeout = 3 if ev == "SessionEnd" else 30  # Codex allows at most 3 s for SessionEnd
        lines += [f"[[hooks.{ev}.hooks]]", 'type = "command"', f"command = '{posix}'",
                  f"command_windows = '{windows}'", f"timeout = {timeout}"]
    return "\n".join(lines) + "\n"


def cursor_shared() -> dict[str, str]:
    models = _models()["cursor"]
    files = {}
    for name in WORKERS:
        a = _agent(name)
        fm = ["---", f"name: {name}", f"description: {_yaml_str(a['description'])}", f"model: {models[a['tier']]}"]
        if a.get("read_only"):
            fm.append("readonly: true")
        fm.append("---")
        files[f".cursor/agents/{name}.md"] = "\n".join(fm) + "\n\n" + _prompt(name)
    return files


def agents_md() -> str:
    return (_header("codex", "AGENTS.header.md") + "\n" + _core() + "\n## If you are the main thread: you are the Orchestrator\n\n"
            + _prompt("orchestrator"))


# ====================================================================== machine-local files

def python_command() -> str:
    return "python" if os.name == "nt" else "python3"


def claude_local() -> dict[str, str]:
    def h(event: str) -> dict:
        return {"type": "command", "command": python_command(),
                "args": ["${CLAUDE_PROJECT_DIR}/kit/engine/flamin.py", "hook", event, "--tool", "claude"],
                "timeout": 30}
    hooks = {
        "PreToolUse": [{"matcher": "^(Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell|Agent|Task)$|^mcp__",
                        "hooks": [h("pretool")]}],
        "PostToolUse": [{"matcher": "*", "hooks": [h("posttool")]}],
        "SessionEnd": [{"hooks": [h("sessionend")]}],
        "Stop": [{"hooks": [h("sessionend")]}],
        "PreCompact": [{"hooks": [h("precompact")]}],
    }
    return {".claude/settings.local.json": json.dumps({"hooks": hooks}, indent=2) + "\n"}


def cursor_local() -> dict[str, str]:
    # Cursor runs project hooks from the project root, through PowerShell on Windows (probed): `.\flamin.cmd` (D-28).
    # Cursor keeps `.\flamin.cmd` (D-28, D-40). Its reply is JSON with exit 0, so no exit-code passthrough is needed.
    launcher, tail = (".\\flamin.cmd", "") if os.name == "nt" else ("./flamin", "")

    def h(event: str, fail_closed: bool = False) -> dict:
        d = {"command": f"{launcher} hook {event} --tool cursor{tail}", "timeout": 30}
        if fail_closed:
            d["failClosed"] = True
        return d
    hooks = {
        "preToolUse": [h("pretool", True)],
        "beforeShellExecution": [h("pretool", True)],
        "beforeMCPExecution": [h("pretool", True)],
        "subagentStart": [h("pretool", True)],
        "postToolUse": [h("posttool")],
        "afterFileEdit": [h("posttool")],
        "afterShellExecution": [h("posttool")],
        "preCompact": [h("precompact")],
        "sessionEnd": [h("sessionend")],
        "stop": [h("sessionend")],
    }
    return {".cursor/hooks.json": json.dumps({"version": 1, "hooks": hooks}, indent=2) + "\n"}


# ====================================================================== render

ALL_TOOLS = ("claude", "codex", "cursor")


def tool_tuple(tool) -> tuple:
    """"all", one tool name, or a list of tool names."""
    if tool == "all":
        return ALL_TOOLS
    return (tool,) if isinstance(tool, str) else tuple(t for t in ALL_TOOLS if t in tool)


def planned(tool="all") -> dict[str, str]:
    tools = tool_tuple(tool)
    files: dict[str, str] = {}
    if "claude" in tools:
        files.update(claude_shared())
        files.update(claude_local())
    if "codex" in tools:
        files.update(codex_shared())
        files["AGENTS.md"] = agents_md()
    if "cursor" in tools:
        files.update(cursor_shared())
        files.update(cursor_local())
        files["AGENTS.md"] = agents_md()
    return files


LOCAL_FILES = {".claude/settings.local.json", ".cursor/hooks.json"}


def render_all(root: Path, tool="all", only_existing: bool = False) -> list[str]:
    changed = []
    for relp, text in sorted(planned(tool).items()):
        target = root / relp
        if only_existing and not target.exists():
            continue
        if (read_text(target) or "").replace("\r\n", "\n") == text and target.exists():
            continue  # a CRLF checkout of the same text is current (D-45)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        changed.append(relp + (" (machine-local)" if relp in LOCAL_FILES else ""))
    return changed


def stale_renders(root: Path, tools="all") -> list[str]:
    out = []
    for relp, text in sorted(planned(tools).items()):
        target = root / relp
        if target.exists() and (read_text(target) or "").replace("\r\n", "\n") != text:
            out.append(relp)
    return out


# Files each tool's adapter owns, for pruning a tool a product does not use. AGENTS.md is shared by Codex and Cursor.
TOOL_PATHS = {"claude": (".claude", "CLAUDE.md"), "codex": (".codex",), "cursor": (".cursor",)}


def tool_files(root: Path, tool: str) -> list[str]:
    out = []
    for item in TOOL_PATHS[tool]:
        p = root / item
        if p.is_dir():
            out += sorted(f.relative_to(root).as_posix() for f in p.rglob("*") if f.is_file())
        elif p.is_file():
            out.append(item)
    return out
