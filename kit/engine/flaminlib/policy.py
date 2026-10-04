"""Built-in policy: protected paths, per-agent paths, shell patterns, gated actions (DESIGN §15.1, §15.3).

Configurable policy files are deferred (DESIGN §15.4); these are the built-in defaults.
"""
from __future__ import annotations

import json
import ast
import re
import shlex
from functools import lru_cache

from .util import KIT_DIR, any_glob, glob_match

# Only the engine writes these (Hard where a pre-write hook exists).
STATE_PATHS = [".flamin/*.json", ".flamin/*.json.sha256", ".flamin/.lock", ".flamin/tmp/**",
               ".flamin/audit/**", ".flamin/cache/**", ".flamin-maintenance/**"]
# Enforcement layer (P-19 / D-30).
ENFORCEMENT_PATHS = ["kit/**", "flamin", "flamin.cmd", ".gitattributes", ".claude/**", ".codex/**", ".cursor/**",
                     "CLAUDE.md", "AGENTS.md", ".git/**", ".flamin/stacks/**"]
SECRET_FILES = [".env", ".env.*", "**/.env", "**/.env.*", "**/*.pem", "*.pem", "**/*.key", "*.key",
                "secrets/**", "**/secrets/**", "**/credentials*", "credentials*", "**/*.p12", "**/*.keystore"]
DEPENDENCY_FILES = ["package.json", "requirements.txt", "requirements-*.txt", "pyproject.toml", "pom.xml",
                    "build.gradle", "build.gradle.kts", "go.mod", "Cargo.toml", "Gemfile", "Podfile",
                    "*.csproj", "Pipfile", "setup.py", "setup.cfg"]


# Kit files that master-kit maintenance mode opens for agents (D-46). Tool config, rules files and .git stay shut.
KIT_FILE_PATHS = ["kit/**", "flamin", "flamin.cmd"]
MAINTENANCE_FLAG = ".flamin-kit-maintenance"


def is_kit_file(p: str) -> bool:
    return any_glob(p, KIT_FILE_PATHS)


def maintenance_mode(root) -> bool:
    """Master-kit maintenance mode: the human-set flag and no product state (D-46)."""
    return (root / MAINTENANCE_FLAG).exists() and not (root / ".flamin").exists()


def agent_has_shell(agent: str) -> bool:
    return "shell" in agent_defs().get(agent, {}).get("tools", [])


def is_state_path(p: str) -> bool:
    return any_glob(p, STATE_PATHS)


def is_enforcement_path(p: str) -> bool:
    return any_glob(p, ENFORCEMENT_PATHS)


def is_secret_file(p: str) -> bool:
    name = p.rsplit("/", 1)[-1]
    if name in (".env.example", ".env.sample", ".env.template"):
        return False
    return any_glob(p, SECRET_FILES)


def is_dependency_file(p: str) -> bool:
    name = p.rsplit("/", 1)[-1]
    return any(glob_match(name, pat) for pat in DEPENDENCY_FILES)


@lru_cache(maxsize=None)
def agent_defs() -> dict:
    out = {}
    d = KIT_DIR / "agents"
    for p in sorted(d.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        if "name" in data and "tier" in data:  # skips models.json
            out[data["name"]] = data
    return out


def agent_may_write(agent: str, relpath: str, info) -> tuple[bool, str]:
    """Per-agent path rules (Hard on Claude Code and Codex, Soft on Cursor)."""
    spec = agent_defs().get(agent)
    if spec is None:
        return False, f"unknown agent '{agent}'"
    for pat in spec.get("may_not_write", []):
        if glob_match(relpath, pat):
            return False, f"{agent} may not write {relpath}"
    for pat in spec.get("may_write", []):
        if pat == "<product-code>":
            if not relpath.startswith(".flamin/") and not (info.profile and info.is_test):
                return True, "product code"
        elif pat == "<tests>":
            if info.profile and info.is_test:
                return True, "test path"
        elif glob_match(relpath, pat):
            return True, f"matches {pat}"
    allowed = ", ".join(spec.get("may_write", [])) or "nothing"
    return False, f"{agent} may write only: {allowed}. {relpath} is outside that."


# ---------------------------------------------------------------- shell analysis

DESTRUCTIVE = [
    (r"\brm\s+(?:-[a-zA-Z]*[rR][a-zA-Z]*\s+|--recursive\s+)+(?:-[a-zA-Z]+\s+)*(?:/|~/?|\$HOME/?|\.\./?|[A-Za-z]:[\\/]?|\*)(?:\s|$|\*)",
     "recursive delete outside the project"),
    (r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*\s+.*\.flamin(?:/|\b)", "wipes .flamin/ state"),
    (r"(?i)Remove-Item\b.*-Recurse\b.*(?:\s[A-Za-z]:\\?\s*$|\s[\\/]\s|\s~|\s\.\.|\$HOME|\$env:USERPROFILE)", "recursive delete outside the project"),
    (r"(?i)Remove-Item\b.*\.flamin", "wipes .flamin/ state"),
    (r"(?i)\b(?:rd|rmdir)\s+/s\b", "recursive delete"),
    (r"(?i)\bdel\s+/[sq]\b.*[A-Za-z]:\\", "recursive delete outside the project"),
    (r"\bgit\s+push\b.*(?:\s--force(?:-with-lease)?\b|\s-f\b|\s\+\S+)", "force push"),
    (r"\bgit\s+(?:filter-branch|filter-repo)\b", "history rewrite"),
    (r"\bgit\s+update-ref\s+-d\b", "history rewrite"),
    (r"\bgit\s+branch\s+-D\s+(?:main|master|develop|release\S*)\b", "deletes a shared branch"),
    (r"\bgit\s+push\s+\S+\s+(?:--delete|:)\S*", "deletes a remote branch"),
    (r"\bgit\s+rebase\b.*\b(?:origin/)?(?:main|master)\b.*&&.*git\s+push", "history rewrite of a shared branch"),
    (r"(?i)\bdrop\s+(?:table|database|schema)\b", "drops a table or database"),
    (r"(?i)\btruncate\s+(?:table\s+)?\w+", "truncates a table"),
    (r"\bmkfs(?:\.\w+)?\b", "disk format"),
    (r"(?i)\bformat(?:-volume)?\s+[A-Za-z]:", "disk format"),
    (r"(?i)\bdiskpart\b", "disk tool"),
    (r"\bdd\s+.*\bof=/dev/", "raw disk write"),
    (r"(?i)\bClear-Disk\b|\bInitialize-Disk\b", "disk wipe"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork bomb"),
    (r"(?:>|\btee\b|\bmv\b|\bcp\b).*\.flamin/(?:state|approvals|locks|modules|stack|models)\.json", "overwrites engine state"),
]
DESTRUCTIVE_RX = [(re.compile(p), why) for p, why in DESTRUCTIVE]

FLAMIN_CMD = r"(?:^|[\s;&|(`'\"])(?:[.\w:/\\-]*[/\\])?(?:flamin(?:\.cmd)?|flamin\.py)\s+"
HUMAN_ONLY = [
    (re.compile(FLAMIN_CMD + r"kit-maintenance\s+authorize-commit\b"), "maintenance commit authorization"),
    (re.compile(FLAMIN_CMD + r"approve\b"), "approve"),
    (re.compile(FLAMIN_CMD + r"upgrade\b"), "upgrade"),
    (re.compile(FLAMIN_CMD + r"doctor\b[^\n;&|]*--clear-stale-lock"), "clear-stale-lock"),
    (re.compile(FLAMIN_CMD + r"doctor\b[^\n;&|]*--update-manifest"), "update-manifest"),
    (re.compile(FLAMIN_CMD + r"kit-maintenance\s+(?:on|off)\b"), "kit-maintenance switch"),
    (re.compile(FLAMIN_CMD + r"[\w-]+\b[^\n;&|]*\s--(?:yes|no)\b"), "gate answer"),
]

GATED_SHELL = [
    ("delete", r"(?:^|[\s;&|])(?:rm|unlink|shred)\s+\S|(?i:\bRemove-Item\b|\bdel\s+\S|\berase\s+\S|\bri\s+\S)|\bgit\s+rm\b|\bgit\s+clean\b"),
    ("dependency", r"\bpip3?\s+install\s+(?!-r\b|--requirement\b|-e\s+\.)|\bpython[\d.]*\s+-m\s+pip\s+install\s+(?!-r\b|--requirement\b|-e\s+\.)"
                   r"|\b(?:npm|pnpm)\s+(?:install|i|add)\s+(?:-\S+\s+)*[@\w]|\byarn\s+add\b|\bpoetry\s+add\b|\buv\s+(?:add|pip\s+install)\b"
                   r"|\bgo\s+get\b|\bcargo\s+add\b|\bdotnet\s+add\s+\S+\s+package\b|\bgem\s+install\b|\bpod\s+install\b|\bmvn\s+dependency:get\b|\bnpx\s+expo\s+install\b"),
    ("migration", r"\balembic\s+(?:upgrade|downgrade)\b|\bflyway\b|\bliquibase\s+update\b|\bprisma\s+migrate\b|\bknex\s+migrate\b"
                  r"|\bmanage\.py\s+migrate\b|\bdb:migrate\b|\b(?:npm|yarn|pnpm)\s+run\s+migrate|\btypeorm\s+migration:run\b|\bsequelize\s+db:migrate\b"),
    ("app-store", r"\bfastlane\s+(?:deliver|pilot|supply|upload_to_\w+)\b|\beas\s+submit\b|\bxcrun\s+altool\b.*--upload|\biTMSTransporter\b|\btransporter\b"),
    ("external-api", r"\b(?:curl|wget|http|httpie)\s+[^\n]*https?://(?!localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])"
                     r"|\bgh\s+(?:api|release|repo|pr|issue)\b"
                     r"|(?i:\bInvoke-(?:WebRequest|RestMethod)\b[^\n]*https?://(?!localhost|127\.0\.0\.1))"
                     r"|\beas\s+build\b|\bgcloud\s+builds\b|\baws\s+\w+|\baz\s+\w+|\bfirebase\s+deploy\b|\bvercel\b|\bnetlify\s+deploy\b"),
    ("secret-change", r"(?:>|>>|\btee\b|\bcp\b|\bmv\b|(?i:Set-Content|Add-Content|Out-File))[^\n]*(?:\.env\b|\.pem\b|\.key\b|secrets/|credentials)"),
    ("release", r"\bgit\s+push\b[^\n]*--tags\b|\bnpm\s+publish\b|\btwine\s+upload\b|\bdocker\s+push\b"),
]
GATED_SHELL_RX = [(k, re.compile(p)) for k, p in GATED_SHELL]

SHELL_WRITE = re.compile(
    r"(?:>{1,2}\s*|\btee\s+(?:-a\s+)?|\bsed\s+-i\S*\s+(?:'[^']*'|\"[^\"]*\"|\S+)\s+|\b(?:cp|mv|install)\s+(?:-\S+\s+)*\S+\s+"
    r"|(?i:Set-Content|Add-Content|Out-File|New-Item|Copy-Item|Move-Item|Remove-Item)\s+(?:-\w+\s+)*|\b(?:rm|touch|truncate)\s+(?:-\S+\s+)*)"
    r"(['\"]?)([^\s'\";|&<>]+)\1")


def human_only(command: str) -> str | None:
    command = _without_document_literals(command)
    for rx, what in HUMAN_ONLY:
        if rx.search(command):
            return what
    return None


def destructive(command: str) -> str | None:
    for rx, why in DESTRUCTIVE_RX:
        if rx.search(command):
            return why
    return None


def gated_shell(command: str) -> str | None:
    if re.search(FLAMIN_CMD, " " + command):
        return None  # engine commands gate themselves
    for kind, rx in GATED_SHELL_RX:
        if rx.search(command):
            if kind == "external-api" and github_read(command):
                continue
            return kind
    return None


def _without_document_literals(command: str) -> str:
    """Exclude constant text written by a Python heredoc to an opened document.

    Execution strings (subprocess, eval, etc.) are deliberately left intact.
    This is only a false-positive filter, not a general shell parser.
    """
    match = re.match(r"\s*@'\r?\n([\s\S]*?)\r?\n'@\s*\|\s*python\s+(?:-B\s+)?-(?=$|[\s;|&])", command)
    if not match:
        return command
    body = match.group(1)
    try:
        tree = ast.parse(body)
    except SyntaxError:
        return command
    lines = body.encode('utf-8').splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    spans = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        handles = set()
        for item in node.items:
            expr = item.context_expr
            if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
                    and expr.func.attr == 'open' and isinstance(item.optional_vars, ast.Name)
                    and any(isinstance(arg, ast.Constant) and arg.value in ('a', 'w') for arg in expr.args)):
                handles.add(item.optional_vars.id)
        # Only direct writes within the with block, without nested execution.
        for statement in node.body:
            call = statement.value if isinstance(statement, ast.Expr) else None
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name) and call.func.value.id in handles
                    and call.func.attr == 'write' and len(call.args) == 1
                    and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
                literal = call.args[0]
                spans.append((offsets[literal.lineno - 1] + literal.col_offset,
                              offsets[literal.end_lineno - 1] + literal.end_col_offset))
    sanitized = body.encode('utf-8')
    for start, end in sorted(spans, reverse=True):
        sanitized = sanitized[:start] + b"''" + sanitized[end:]
    return command[:match.start(1)] + sanitized.decode('utf-8') + command[match.end(1):]


def github_read(command: str) -> bool:
    """Recognize literal GitHub reads only; compound/dynamic requests remain gated."""
    if re.search(r'[;|`\n]|\$\(|&&|\|\|', command):
        return False
    if re.search(r'(?i)--(?:data\S*|upload-file|form\S*)\b|\s-[dFT]\b|-(?:Body|InFile)\b', command):
        return False
    methods = re.findall(r'(?i)(?:-Method\s+|--request\s+|-X\s*)([\w]+)', command)
    if any(m.upper() not in ('GET', 'HEAD') for m in methods):
        return False
    if re.search(r'(?i)\bgh\s+api\b', command):
        # GET with no fields is the only supported gh API read form.
        return bool(re.fullmatch(r'\s*gh\s+api\s+(?:--method\s+(?:GET|HEAD)\s+)?'
            r'(?:/)?repos/[\w.-]+/[\w.-]+/(?:releases|branches|commits|tags)(?:/[\w./%-]+)?\s*', command))
    urls = re.findall(r'https?://[^\s\'\"}]+', command)
    if len(urls) != 1 or not re.fullmatch(
            r'https://api\.github\.com/repos/[\w.-]+/[\w.-]+/(?:releases|branches|commits|tags)(?:/[\w./%-]+)?', urls[0]):
        return False
    # Exactly one recognized HTTP invocation, with no second shell command.
    calls = re.findall(r'(?i)\b(?:curl|wget|http|httpie|Invoke-WebRequest|Invoke-RestMethod)\b', command)
    if len(calls) != 1 or calls[0].lower() not in ('curl', 'invoke-webrequest', 'invoke-restmethod'):
        return False
    if calls[0].lower() == 'curl' and re.search(r'\s-[A-Za-z]*[dFTX][A-Za-z]+\b', command):
        return False
    if calls[0].lower() == 'curl':
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False
        if not tokens or tokens[0] != 'curl':
            return False
        i = 1
        while i < len(tokens):
            token = tokens[i]
            if token in ('-H', '--header', '-X', '--request'):
                i += 2
                continue
            if token not in ('-I', '--head', '-s', '-S', '-sS', '--silent', '--show-error', '--fail', urls[0]):
                return False
            i += 1
    else:
        # A literal URI, method and optional literal header pair; expressions stay gated.
        literal = r'''(?:'[^'$`]*'|"[^"$`]*")'''
        endpoint = re.escape(urls[0])
        if not re.fullmatch(r'(?i)\s*(?:\$\w+\s*=\s*)?Invoke-(?:WebRequest|RestMethod)\s+'
                r'(?:-Method\s+(?:GET|HEAD)\s+)?-Uri\s+(?:' + endpoint + r'|"' + endpoint + r'"|\'' + endpoint + r"\')"
                r'(?:\s+-Headers\s+@\{\s*' + literal + r'\s*=\s*' + literal + r'\s*\})?\s*', command):
            return False
    return True


def shell_write_targets(command: str) -> list[str]:
    """Heuristic list of paths a shell command writes to."""
    out = []
    for m in SHELL_WRITE.finditer(command):
        t = m.group(2).strip()
        if t and not t.startswith("-") and t not in ("/dev/null", "NUL", "$null", "&1", "&2"):
            out.append(t)
    return out


def normalize_flamin_args(command: str) -> str | None:
    """Arguments after the flamin launcher, normalised, for ask tokens."""
    m = re.search(FLAMIN_CMD + r"(.*)$", command.strip().splitlines()[0] if command.strip() else "")
    if not m:
        return None
    rest = m.group(1)
    rest = re.split(r"\s*(?:&&|\|\||;|\|)\s*", rest)[0]
    try:
        parts = shlex.split(rest, posix=True)
    except ValueError:
        parts = rest.split()
    return " ".join(parts)
