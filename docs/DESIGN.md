# flamin Design

**Status:** Approved on 2026-09-28 (decisions D-01 to D-47) | **Kit version:** v1 | **Date:** 2026-09-28 | This is the design followed by the initial kit implementation.

---

## 0. How to read this document

Every mechanism in this document carries one of three strength labels.

| Label | Meaning | Everyday picture |
|---|---|---|
| **Hard** | A machine check blocks the action before it lands. No model judgment is involved. | A locked door. |
| **Soft** | An instruction in a rules file or agent prompt. The model is asked to follow it. Nothing blocks a breach at that moment. | A sign on the door. |
| **Heuristic** | A machine check based on patterns. It catches the common case. It can miss clever cases or block a harmless one. | A motion sensor. |

A mechanism can be Hard in one tool and Soft in another. Section 5 shows where and why.

Words used often:

- **Kit**: the flamin files that are the same for every product (engine, profiles, agents, adapters, launchers).
- **Product project**: a copy of the kit that builds one real product. It has its own `.flamin/` folder.
- **Engine**: the small Python program that owns all state and all checks.
- **Adapter**: the thin layer that turns engine features into native features of one AI tool (Claude Code, Codex or Cursor).
- **Tool**: one of the three AI coding tools. A session always uses one tool.

**The three guides in `docs/`.** `Collaborative Workflow.md`, `Generated Code Architecture Summary.md` and `Tips and Tricks.md` are read-only background. They describe another platform. Their features (for example session rollback by double-click, file mentions, a workbench, a design page) are not flamin features. Where a guide conflicts with this document, this document wins. Section 19 shows how their useful lessons work in flamin.

---

## 1. What flamin is

flamin is a kit for building software with a team of AI agents. A human talks to it in plain language. Eight agents do the work in a fixed order, like stations on a factory line. A small engine keeps the line honest: it knows which step the product is on, which files are locked, and which actions need a human "yes" or "no".

The design has three big ideas:

1. **One engine, many tools.** All rules and all state live in one tool-neutral engine. Claude Code, Codex and Cursor only get thin adapters. A product can move between tools without losing its place.
2. **Any stack.** Layer names, file types, lock markers and templates come from a stack profile. Java is only one example. Python, TypeScript, JavaScript, React, mobile and "no layers" are all first-class.
3. **Honest guarantees.** Each rule says how strong it really is. Where a tool lacks a feature, the design names the fallback instead of pretending.

---

## 2. Kit layout and portability

### 2.1 Folder layout

```
<project root>/
├── flamin                 # POSIX launcher (Linux, macOS), one line
├── flamin.cmd             # Windows launcher, one line
├── .gitattributes         # line endings per path (D-45)
├── kit/                   # everything that is the same for every product
│   ├── VERSION            # kit version, e.g. "v1"
│   ├── MANIFEST           # hash of every kit file (P-19)
│   ├── rules/             # core.md, the one source for CLAUDE.md and AGENTS.md (P-13)
│   ├── engine/            # the flamin engine (Python 3 standard library only)
│   ├── agents/            # eight agent definitions, tool-neutral format
│   ├── adapters/
│   │   ├── claude/        # templates rendered into .claude/ and CLAUDE.md
│   │   ├── codex/         # templates rendered into .codex/ and AGENTS.md
│   │   └── cursor/        # templates rendered into .cursor/
│   ├── stacks/<profile>/  # shipped stack profiles (read only master copies)
│   ├── githooks/          # pre-commit templates, rendered per machine into .git/hooks/
│   └── ci/                # sample CI workflow
├── docs/                  # this design, the three read-only guides (§0), README, WORKFLOW, VERIFICATION, BUILD_LOG
├── .flamin/               # product state, created by `flamin init`, never in the master kit
└── <product code>         # layout comes from the chosen stack profile(s)
```

Files rendered by adapters (`.claude/`, `CLAUDE.md`, `.codex/`, `AGENTS.md`, `.cursor/`) are build outputs of `flamin init`. They hold no rules of their own. Running `flamin init --tool <name>` again re-renders them safely.

**Shared files versus machine-local files (P-04).** A committed file must work on every OS. Any file that has to name a Python command is **machine-local**: `flamin init` renders it for the current OS, git ignores it, and `flamin doctor` re-renders it when the OS or the Python command changes. Codex is the one exception, because its hook config, inline in `.codex/config.toml` (D-39), carries both `command` and `command_windows` in one shared file.

| File | Shared or machine-local | Why |
|---|---|---|
| `.claude/agents/`, `.claude/settings.json` (agent, env, permissions), `CLAUDE.md` | Shared | No Python command inside |
| `.claude/settings.local.json` (hooks) | Machine-local | Hook command differs per OS |
| `.codex/agents/`, `.codex/config.toml`, `AGENTS.md` | Shared | `command_windows` covers Windows; hooks are inline in `config.toml` (D-39) |
| `.cursor/agents/`, `AGENTS.md` | Shared | No Python command inside |
| `.cursor/hooks.json` | Machine-local | Hook command differs per OS |
| `.git/hooks/pre-commit` | Machine-local | Git never commits it anyway |

Trade-off: a teammate who clones the product has no hooks until `flamin init` runs on that machine. The hook health check (§4.6) catches this on the first session.

### 2.2 The product state folder

```
.flamin/
├── state.json         # phase, version, baseline, per-module steps, build number
├── locks.json         # lock manifest, rebuilt from inline markers
├── modules.json       # module -> contract map
├── stack.json         # chosen stack profile or profiles
├── models.json        # tier -> model per tool
├── approvals.json     # open requests, answers, approved hashes, assumption status (P-20)
├── stacks/<profile>/  # the product's own copy of each approved profile
├── business/          # overview, glossary, actors, journeys, rules, states
├── analysis/<slug>/   # requirements.md, plan.md
├── technical/         # overview, capabilities
├── design/<module>/   # design specs (generator input)
├── decisions/         # stack.md, assumptions.md, baseline-amendments.md, approvals
├── sessions/          # handoff files
├── audit/             # YYYY-MM-DD.jsonl, excluded from git
└── versions/          # v1.md, v2.md, ...
```

Shipped profiles live in `kit/stacks/`. When the Architect's stack choice is approved, the engine copies the chosen profile into `.flamin/stacks/<profile>/`. The product then owns its copy and can tune it without touching the kit. (P-01.) A change to the product's copy goes through the same gate as a new stack profile (P-19).

### 2.3 Master kit and product projects

The folder `flamin_v3_Claude` is the **master kit**. It is a clean template and never hosts a real product.

- It holds no product code, no `.flamin/` state, no audit logs, no caches and no secrets. (Hard at release of the kit: `flamin doctor --kit` fails if any of these exist.)
- To start a product: copy the folder, rename it (for example `flamin_cinema`), open it in any tool, and say "start a new project" (or run `flamin init`). This creates a fresh `.flamin/`.
- On every startup the Orchestrator runs `flamin status`. If state exists, it asks: "Continue product `<name>`, or start a new product?" A new product in a folder that already has one is refused, with advice to copy the master kit again. One folder never mixes two products. (Hard: `flamin init` refuses to overwrite existing state.)
- **Master-kit maintenance mode (D-46).** It turns on only when a human runs `flamin kit-maintenance on` in a terminal (human-only through the hook). This writes a git-ignored flag, `.flamin-kit-maintenance`. With the flag and no `.flamin/`, the product flow does not apply and D-30 is lifted for kit files only. Kit changes must pass the engine tests and refresh `kit/MANIFEST`. `flamin init` refuses while the flag is set. "No product found" alone never enables this mode, because a freshly copied product folder looks the same before `flamin init`. The master kit keeps shipping the rendered adapters, so the plain-language start works. How maintenance sessions get write tools is not decided yet. (Hard for the switch: only a human can set the flag, and `flamin doctor --kit` fails while it exists, so a shipped kit never carries it.)

### 2.4 Paths

- All paths in kit files, state files, handoffs and audit logs are relative to the project root. (Hard: the engine stores paths relative and `flamin doctor` flags any absolute path, machine name or user name it finds in kit and state files. The scan is Heuristic.)
- Hook commands in tool config use each tool's own "project root" mechanism (Section 4).

### 2.5 `flamin init`

1. Checks the kit runtime (Python 3.11 or newer) and prints the working Python command.
2. Checks for a git repo. Creates one if missing, or warns if creation fails, because the backstop needs git.
3. Checks for leftover state. Refuses if a product already lives here. Also refuses while the kit-maintenance flag is set (D-46).
4. Creates `.flamin/` with empty templates and `state.json` at phase 0 (intake), version v1.
5. Writes `.git/hooks/pre-commit` for the current OS (one line calling the matching launcher), so the backstop is active. It writes the file with LF line endings and the executable bit itself, because git never checks this file out, so `.gitattributes` cannot reach it (D-45).
6. Renders exactly one tool adapter, including its machine-local hook files: `--tool claude|codex|cursor` if given, otherwise the detected tool (Claude Code when `CLAUDECODE=1` or `CLAUDE_PROJECT_DIR` is set), otherwise Claude Code. The choice is recorded in `state.json` as a one-item `tools` list. Inactive adapter directories and root prompts from a copied master kit move to `.flamin/inactive-adapters/`, outside tool discovery. A later `--tool` switch on an existing product opens a human approval request before changing adapters or state (D-48).
7. Writes `.gitignore` entries (Section 2.7).

Every step is safe to run twice.

### 2.6 `flamin doctor`

Reports anything that blocks the kit on this machine: Python command that works (`python3` on Linux and macOS, `python` on Windows), Python version and whether it is past end of life, the Windows Store stub (a `python` or `python3` that only opens the Store), git presence, hooks path, adapter files present and current, stale lock file, hooks that the tool has not yet trusted (Codex requires a trust review before non-managed hooks run; Claude Code and Cursor require a trusted workspace), absolute paths, and state checksum mismatches. `flamin doctor --kit` adds the master-kit cleanliness check, and fails while the kit-maintenance flag exists (D-46). `flamin doctor` also checks that Codex accepts the `[agents]` keys (`codex exec --strict-config`, D-38), that the rendered Codex hook runs on the installed Codex (D-40), and that `flamin` and the hook templates keep the executable bit (D-45).

### 2.7 `.gitignore`

```
.flamin/audit/
.flamin/cache/
.flamin/tmp/
.flamin/.lock
.flamin-backup-*/
.flamin-kit-maintenance
.claude/settings.local.json
.cursor/hooks.json
.env
.env.*
*.pem
*.key
secrets/
```

State, decisions, sessions and versions are committed, so a second person or a second tool picks up the same product.

### 2.8 Kit upgrade

The kit uses the same version scheme as products (v1, v2, ...). `kit/VERSION` holds it. `flamin upgrade --from <path to newer master kit>`:

1. Shows the old and new kit versions and the list of kit files that will change (three-line preview, §7.1).
2. Replaces only `kit/`, the two launchers and the rendered adapter files.
3. Never touches `.flamin/`. (Hard: the upgrade code has no write path into `.flamin/`.)
4. If the new kit needs a different state format, upgrade stops and reports the needed migration. A migration is a separate step behind an Approval Gate: `flamin upgrade --migrate` first copies `.flamin/` to `.flamin-backup-<old kit version>/`, then runs a unit-tested migration. Never automatic. (P-02.)

---

## 3. Core engine

### 3.1 Kit runtime choice

**Python 3 standard library, version 3.11 or newer (P-03).** Python 3.11 is the oldest release that still gets security fixes for a useful time (security-only phase, end of life scheduled for October 2027) and the first with `tomllib` in the standard library, which lets specs be TOML (§14.1). Older floors buy nothing: 3.8 ended on 2024-10-07, 3.9 on 2025-10-31, and 3.10 ends in October 2026. Versions in the security-only phase get no new binary installers, so a fresh install today is normally 3.13 or 3.14; the floor only decides which older machines are still accepted. Verified trade-offs: Ubuntu 22.04 LTS ships `python3` 3.10.6 by default on amd64 (3.10.4 on arm64 and other ports), and the `/usr/bin/python3` that macOS provides through Xcode or the Command Line Tools is, per the Python macOS guide, usually older and incomplete. On those machines `flamin doctor` tells the human to install a current Python. The engine entry file checks the version first, using syntax that even old Python can read, so a too-old Python gets a clear message instead of a crash. `flamin doctor` also warns when the running Python has passed its end of life. Python is one installer away on every OS. The standard library already has everything the engine needs: JSON, regex, file locking by exclusive create, atomic rename (`os.replace`), hashing, `string.Template` for generation, `argparse`, and `unittest`. No third-party packages means no internet and no dependency drift. The same code runs on all three operating systems. Node or Go would need either an extra install or a compiled binary per platform.

The standard library reads TOML but has no YAML reader, so design specs are TOML (§14.1, P-05). The engine only reads specs; agents write them as text. Inside the engine, any need to start Python again uses `sys.executable`, never a hard-coded command name.

### 3.2 What the engine owns

The engine is the only writer of `.flamin/*.json`. Agents never edit these files directly. (Hard where the tool has a pre-write hook: PreToolUse denies agent writes to `.flamin/*.json`. Heuristic backstop: each state file has a checksum sidecar written by the engine, and `check-phase` in the pre-commit hook fails on a mismatch. The checksum ignores CRLF versus LF only, so a Windows checkout does not trip it; any other change still fails (D-45). This makes tampering visible, not impossible.)

`state.json` example:

```json
{
  "product": "cinema",
  "kit_version": "v1",
  "version": 2,
  "build": 17,
  "phase": 2,
  "baseline": { "confirmed": true, "at": "2026-10-02T09:41:00Z", "amendments": 1 },
  "modules": {
    "booking": { "steps_done": [6, 7, 8], "self_test": null, "lease": "developer@s-81f2" },
    "catalog": { "steps_done": [6, 7, 8, 9, 10], "self_test": "pass", "lease": null }
  },
  "phase3": { "integration_test": null, "architecture_review": null },
  "open_requests": ["R-0012"]
}
```

### 3.3 Commands

All commands are `flamin <verb>`. Per-tool wrappers (Section 4) only call these.

| Command | Purpose |
|---|---|
| `init` | Create `.flamin/`, render adapters, set up git hooks. |
| `status` | Show product, version, phase, current step, open requests. |
| `doctor` | Report blockers on this machine (Section 2.6). |
| `resume` | Show the last completed step and the next step from `state.json` and the latest handoff. |
| `intake` | Record Step 0 answers; report missing mandatory items. |
| `analyze` | Step 1 records: business input and testable requirements. |
| `model` | Steps 2 to 4 records: modules, data model, aggregates. (Domain modeling, not AI model choice.) |
| `baseline-review` | Step 5 checklist; asks the human to confirm the baseline. |
| `baseline-amend` | Logged change to a confirmed baseline. Approval Gate. |
| `plan` | Step 6: interface batches of 5 or fewer, in dependency order. |
| `design` | Step 7: record a design spec for a batch. |
| `generate` | Run `flamin-generate` on a spec (Section 14). |
| `lint` | Run `flamin-lint` on specs, portability and drift. |
| `develop` | Step 8: take the module lease and record progress. |
| `wire` | Step 9: record cross-module contract wiring and mocks. |
| `module-done` | Step 10: record Tester results; release the module lease. |
| `integration-test` | Step 11. Needs every module at `self_test: pass`. |
| `architecture-review` | Step 12: full boundary and data model scan. |
| `release` | Step 13: build the release proposal and open the release gate. |
| `cr-new` | Add a change request to the open version, or to the next one once the open version's CR list is approved (P-22). |
| `cr-approve` | Approval Gate for the CR list of a version. |
| `approve` | Answer any open approval request (yes or no). |
| `audit` | Show the audit log as plain readable lines; `--stats` shows steps per agent per task. |
| `upgrade` | Update kit files from a newer master kit (Section 2.8). |
| `check-lock` | Lock check on a proposed change or on the staged diff. |
| `check-boundary` | Module boundary check on a proposed change or staged diff. |
| `check-phase` | Phase and step order check; state checksum check. |

Extra commands this design adds:

| Command | Purpose |
|---|---|
| `hook <event>` | Single entry point for all tool hooks. Reads the tool's JSON on stdin, runs checks, prints the tool's native reply. |
| `handoff` | Write or validate a handoff file (Section 6.6). |
| `rebuild-locks` | Rebuild `locks.json` from inline markers. |
| `check-staged` | Run check-lock, check-boundary and check-phase on the staged git diff. Used by the pre-commit hook and CI. |
| `kit-maintenance on` / `off` | Human-only switch for master-kit maintenance mode (D-46). |

### 3.4 State safety

- **Atomic writes.** Every write to `.flamin/*.json` goes to a temp file in the same folder, is flushed to disk, then renamed over the old file with `os.replace`. A crash leaves either the old file or the new file, never half a file. (Hard.)
- **One writer at a time.** Every state change first creates `.flamin/.lock` with exclusive create (works on Linux, macOS and Windows). The lock holds the process id, host-free session id and start time. A second writer waits up to 10 seconds, then fails with: "Another flamin command is writing state (session s-81f2, started 12:01:05). Try again, or run `flamin doctor` if it looks stuck." It never overwrites. (Hard.)
- **Stale locks.** A lock older than 120 seconds whose process is gone is reported by `flamin doctor`. Clearing it needs `flamin doctor --clear-stale-lock`. The engine never steals a lock on its own. (Hard.) Build note: on Windows the "is the process gone" check must not use `os.kill(pid, 0)`, because there signal 0 is `CTRL_C_EVENT` and would interrupt live processes. The build uses a read-only check, for example `OpenProcess` through the standard library `ctypes`.
- **Audit appends** use the same lock (Section 15.2). If the lock cannot be taken within the wait, the line goes to `.flamin/audit/spill-<session>.jsonl`, and the next successful append merges it. Audit lines are never dropped. (P-26.)

### 3.5 Idempotency

Every command checks state before acting. Running `module-done booking` twice reports "already done" the second time. Running `generate` twice on an unchanged spec produces identical files. Running `init` twice changes nothing. (Hard: each command has a "no change needed" path, covered by unit tests.)

---

## 4. Tool adapters

Adapters translate engine features into each tool's native features. **No business rule lives in an adapter.** Every hook calls `flamin hook <event> --tool <name>`, and every custom command calls a `flamin <verb>`.

**How a deny travels (D-40).** A deny goes back as JSON in each tool's own reply schema; the exit code is secondary. Codex and Cursor get a JSON deny with exit 0, because on Windows both run hooks through PowerShell, which turns exit code 2 into 1, and Codex then treated the hook as failed and let the call through (probe B4, VERIFICATION.md §3). Claude Code gets a JSON deny plus exit 2, with the reason also on stderr (exec form, no shell).

### 4.1 Claude Code adapter

- **The Orchestrator runs as the main session.** The shared `.claude/settings.json` sets `"agent": "orchestrator"`, so the main thread takes the Orchestrator's prompt, tools and model. Its `tools` list uses the main-thread-only allowlist `Agent(business, analyst, architect, planner, designer, developer, tester)`, so it can start only the seven flamin agents. (Hard.)
- `.claude/agents/<agent>.md` for the seven workers: `name`, `description`, `tools` or `disallowedTools`, and `model`. None lists `Agent`, so none can start a sub-agent. (Hard.) This matters: by default Claude Code lets sub-agents nest up to three layers.
- Second layer in the shared `.claude/settings.json`: `env.CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH = "1"` (nesting off), `env.CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS = "10"` (the tool refuses an eleventh running sub-agent; default is 20; needs v2.1.217 or later), and `permissions.deny` for `Agent(fork)`, `Agent(Explore)`, `Agent(Plan)`, `Agent(general-purpose)` and `Agent(claude)`, so built-in helpers and conversation forks cannot stand in for flamin agents. (Hard.)
- Model choice: agent files use full model IDs, which the `model` field accepts. `claude-haiku-4-5-20251001` is verified by a live launch on Claude Code 2.1.228 (§5.4). Prefer full IDs over aliases such as `haiku`, which gave the same model in the probe but can move to a newer one. For a Developer task on Deep, the Orchestrator passes the per-invocation `model` parameter, which takes precedence over the agent file. If an organization allowlist blocks a model, Claude Code substitutes one and shows a warning; `/tasks` shows the model each sub-agent really runs on.
- Agent teams stay off: with agent teams enabled, a named sub-agent launches as a teammate instead. The Orchestrator never passes a `name`. (Soft, low risk.)
- No per-tool custom commands (P-09). The human speaks plain language; the Orchestrator maps it to `flamin <verb>`.
- `.claude/settings.local.json` hooks (machine-local, §2.1):
  - `PreToolUse` on `Edit|Write|Bash|PowerShell|Agent` and MCP tools: `flamin hook pretool --tool claude`.
  - `PostToolUse` on all tools: `flamin hook posttool --tool claude`.
  - `SessionEnd` and `Stop`: `flamin hook sessionend --tool claude` (docs-as-memory reminder).
  - `PreCompact`: `flamin hook precompact --tool claude` (handoff reminder, Section 6.6).
- Hook commands use **exec form** (`command` plus `args`, no shell), so Git Bash versus PowerShell on Windows does not matter: `"command": "python3"` on Linux and macOS, `"command": "python"` on Windows, with `args` starting `${CLAUDE_PROJECT_DIR}/kit/engine/flamin.py`. Paths stay relative to the project.
- Deny replies use both a JSON `permissionDecision: "deny"` and exit code 2, because in Claude Code a hook that fails or times out does not block by itself. The reason is also written to stderr (D-40).
- `CLAUDE.md`: short table of contents plus the Soft rules. It points to `.flamin/` instead of copying it.

### 4.2 Codex adapter

- `.codex/agents/<agent>.toml`: the seven worker agents with `name`, `description`, `developer_instructions`, `model`, `model_reasoning_effort` and `sandbox_mode = "workspace-write"` for every agent that writes, which today is all seven (D-41). Their writes are limited by the per-agent path rules, and the hook denies every shell call from an agent whose neutral tool list has no `shell` (Business, Analyst, Architect, Planner). The Orchestrator runs as the main thread, with its instructions in `AGENTS.md`. Any copied `.codex/agents/orchestrator.toml` is archived because it would expose the Orchestrator as a sub-agent. The launch gate also denies any spawn of an agent named `orchestrator` (D-48).
- `.codex/config.toml`: `[agents] max_concurrent_threads_per_session = 10` (`max_threads` is a documented legacy alias) and `max_depth = 1`. `max_depth` is no longer in the Codex docs but 0.153.4 accepts it; it stays as extra protection while `flamin doctor` confirms Codex accepts it. The Hard depth control on Codex is the engine launch gate (D-38). Hooks live only inline in this file, with TOML `command_windows`. No `.codex/hooks.json` is rendered, following the Codex advice of one representation per layer (D-39):
  - `PreToolUse` on `Bash`, `apply_patch` (matches `Edit|Write`), `collaborationspawn_agent` (spawn) and MCP tools. Matchers are regular expressions that must match the **whole** tool name (probed, §5.4): `Agent` and `spawn_agent` do not match a spawn, and broad patterns such as `.*agent.*` also catch `collaborationwait_agent`. The `apply_patch` matcher is not yet probed.
  - `SubagentStart` (second launch check: carries top-level `agent_type` and `agent_id`).
  - `PostToolUse` on the same tools.
  - `SessionEnd` and `Stop`.
- Hook commands resolve the project root from git (as the Codex docs advise, because Codex runs hooks with the session's working directory): `command` calls `./flamin hook ...` and `command_windows` is `& "$(git rev-parse --show-toplevel)\flamin.cmd" hook ... ; exit $LASTEXITCODE`. This replaces D-28's `.\flamin.cmd` for Codex only (D-40). On Windows Codex 0.153.4 runs `command_windows`, not `command`, through PowerShell (probed, §5.4), so the command is PowerShell syntax. A public issue reports that Codex 0.155.0 launches hooks through `cmd.exe`; this is not verified, so `flamin doctor` proves the rendered hook runs on the installed Codex, and the heartbeat (§4.6) catches a silent failure (D-40). One shared file works on every OS.
- `AGENTS.md`: short table of contents plus the Soft rules, rendered from `kit/rules/core.md` (P-13). Codex joins `AGENTS.md` files from the project root down to the working folder and stops at 32 KiB combined by default, so flamin keeps the file short.
- No custom commands (P-09). Codex custom prompts are deprecated and live only in the user's home folder, so they cannot ship with a product anyway.
- Codex does not support "ask" from `PreToolUse`. An unsupported reply marks the hook as failed and **lets the call continue**. So the Codex adapter never replies "ask". Gated actions are always denied, and the human answers the gate through the engine (Section 15.3).
- Codex skips non-managed hooks until the human trusts them in `/hooks`. `flamin doctor` warns until trust is given.
- **Trust is lost on every hook change (probed, §5.4).** Codex stores trust in the user's `~/.codex/config.toml`, one entry per hook, keyed by file, event and position, holding a sha256 hash of the hook. When a hook changes, its hash no longer matches and Codex **skips it without any warning**. Because the key includes the position, moving a hook should also lose its trust (inferred from the key format, not tested). So after every `flamin init`, `flamin init --tool codex` re-render or `flamin upgrade` that changes the hook tables in `.codex/config.toml` (D-39), flamin tells the human to re-trust the hooks in `/hooks`. Until then the heartbeat check (§4.6) is the only signal (P-18).
- `permission_mode` in the hook input varies: `default` in an interactive session, `bypassPermissions` under `codex exec`. The engine never assumes one value.
- Setup note for Windows: the `codex.exe` bundled in `~/.codex/.sandbox-bin` lacks `codex-code-mode-host.exe`, so tool calls fail in its terminal. The copy in `~/.codex/plugins/.plugin-appserver` includes it and works (both 0.153.4). The ChatGPT app may replace that folder on update. This is a machine setup issue, not a kit rule; the README first-run check mentions it.

### 4.3 Cursor adapter

- `.cursor/agents/<agent>.md`: the seven worker agents with `name`, `description`, `model`, and no `readonly` flag on any agent that writes, which today is all seven (D-41). Cursor's `readonly` also blocks file edits and state-changing shell commands, which these agents need. The Orchestrator runs in the main chat with instructions in `AGENTS.md`. Any copied `.cursor/agents/orchestrator.md` is archived. `subagentStart` denies any launch of an agent named `orchestrator` (D-48).
- `.cursor/hooks.json` (version 1):
  - `preToolUse` with **no matcher** (every tool), with `failClosed: true`. The engine acts on `Write`, `Delete`, `Shell`, `Task` and MCP tools, and treats any unrecognised tool that carries a `file_path` as a write: it logs the call and runs the checks it can, never assuming the call is safe (P-15). Reason: the payload is only probed with one model (§5.4), and another model may use an edit tool with a different name.
  - `beforeShellExecution` and `beforeMCPExecution` with `failClosed: true` (these two support "ask"). flamin never uses their "ask" for gates: Cursor hook input has no permission-mode field, so gates always deny and send the human to the terminal (D-42).
  - `subagentStart` (phase gate on agent launch, model check, depth check).
  - `postToolUse`, `afterFileEdit`, `afterShellExecution` (audit and post-edit detection).
  - `sessionEnd` and `stop` (reminders).
- Project hooks run from the project root. `.cursor/hooks.json` is machine-local (§2.1): `./flamin hook pretool --tool cursor` on Linux and macOS, `.\flamin.cmd hook pretool --tool cursor` on Windows. On Windows Cursor runs hook commands through **PowerShell** (probed, §5.4), so commands must be valid PowerShell: never syntax that works only in cmd or only in bash, and a `.cmd` file in the current folder needs the `.\` prefix (P-17). With `failClosed: true`, a broken command blocks loudly instead of letting calls through.
- Several hooks on the same event run **in parallel**, with no guaranteed order (probed, §5.4). No hook may depend on another hook's result or order.
- Every Cursor hook payload includes `user_email`. The engine removes it before storing or logging any payload (§15.2).
- **Cursor reads the other tools' files (D-44).** Cursor also loads sub-agents from `.claude/agents/` and `.codex/agents/` (`.cursor/` wins on a name clash), and, while its Third-Party Imports setting is on (the default), Claude Code hook files such as `.claude/settings.local.json`. So Claude Code's `orchestrator` agent can appear as a Cursor sub-agent, and one tool call may reach the engine twice. The engine answers any Cursor payload (it carries `cursor_version`) in Cursor's format, whichever file sent it, and denies launches of `orchestrator`. Gate requests and audit lines are idempotent per tool call: the key is `tool_use_id`, else `generation_id` plus hook event plus a hash of the tool input. `generation_id` alone is not enough, because it changes per user message, not per tool call. Blocking stays safe, because Cursor merges hook replies so that any deny wins. Probe again once the Cursor CLI is available.
- No `.cursor/rules/` file: Cursor reads the root `AGENTS.md` natively (P-13), so the Soft rules load once, not twice.
- No custom commands or custom modes (P-09). Named agents can already be called with the documented `/name` syntax.

### 4.4 Universal backstop

A git pre-commit hook, rendered by `flamin init` from `kit/githooks/` into `.git/hooks/pre-commit` for the current OS, runs `flamin check-staged`. It works the same for every tool, and also for a human editing by hand. A sample CI workflow in `kit/ci/` runs the same checks on each pull request, on a matrix of Linux, macOS and Windows runners. CI has no staged diff, so it calls `flamin check-staged --range <base>..<head>` on the pull request's commits (P-24). Build note: Git for Windows runs hooks through its own bundled shell, so the Windows pre-commit hook is proven by the backstop acceptance test, not assumed.

The backstop is Hard for anything that reaches a commit. It is after the fact within a session: a bad edit can sit on disk until the commit. It can also be skipped with `git commit --no-verify`, which CI then catches.

### 4.5 Launchers

All logic lives in the engine. Each launcher is one line.

`flamin` (Linux, macOS):
```sh
#!/bin/sh
exec python3 "$(dirname "$0")/kit/engine/flamin.py" "$@"
```

`flamin.cmd` (Windows):
```bat
@python "%~dp0kit\engine\flamin.py" %*
```

Line endings and file modes come from the root `.gitattributes` (D-45): LF for `flamin`, `kit/githooks/*` and `.flamin/**/*.json`; CRLF for `*.cmd`. Git on Windows would otherwise check files out with CRLF, which breaks `/bin/sh` for the POSIX launcher. `flamin` and the hook templates are committed as mode 100755, so a Linux or macOS clone of a kit committed from Windows can still run them (inferred risk, not tested on the Windows build machine).

**Why these two commands (P-04).**

- **Linux and macOS: `python3`.** PEP 394 (status: Active) expects Unix-like systems, macOS included, to install a `python3` command whenever Python 3 is installed, while `python` may be missing or may point elsewhere. The Python macOS guide confirms recent macOS has `/usr/bin/python3`, and a python.org install puts its own `python3` first on PATH.
- **Windows: `python`.** The official Windows guide names `python` as the recommended command for the Python install manager. The manager also adds `py` and `pymanager`, plus a `python3` command that exists only to catch POSIX habits and is "not meant to be widely used or recommended". The older full installer and the old `py` launcher are both deprecated since 3.14, and the full installer adds `python` to PATH only when that option is ticked. On a real Windows machine in this project, only `python` worked; `python3` was the Store shortcut and `py` was missing.
- A one-line launcher cannot try a second command. If the chosen command is missing, the human fixes PATH once, guided by `flamin doctor` and the README's first-run check (`python --version` on Windows, `python3 --version` elsewhere).

### 4.6 Hook health check (P-07)

A hook that fails to start (wrong Python command, untrusted hook, missing machine-local file) is silent on Claude Code and Codex: the tool reports a non-blocking error and carries on. The gates would quietly become Soft.

The fix: every hook call updates `.flamin/tmp/heartbeat-<tool>`. The Orchestrator's first action in every session is `flamin status`, which runs through the shell and therefore through the pre-shell hook. If the heartbeat was not updated in the last few seconds, `flamin status` prints a loud warning: "flamin hooks are not running in this session. Gates are Soft until this is fixed. Run `flamin doctor`." The Orchestrator must stop and show this to the human. (Heuristic detection, but it turns a silent failure into a visible one. On Cursor, `failClosed: true` also blocks calls when a hook cannot run.)

---

## 5. Capability matrix

Checked against the full official pages on 2026-09-24. Links are in Appendix B. Cells marked "probed" were confirmed by live probes on Windows the same day (§5.4); a probe result overrides the docs. **S** = Supported, **P** = Partial, **N** = Not supported, **U** = Unknown.

| Capability | Claude Code | Codex | Cursor |
|---|---|---|---|
| Agent definitions | **S** `.claude/agents/*.md` [C2] | **S** `.codex/agents/*.toml` [X2] | **S** `.cursor/agents/*.md` [U2] |
| Per-agent tool allowlists | **S** `tools`, `disallowedTools` [C2] | **P** `sandbox_mode`, `mcp_servers`, skills per agent; no per-tool list in docs [X2] | **P** `readonly` flag only [U2] |
| Pre-write blocking hook | **S** `PreToolUse` on `Edit`/`Write`, deny [C1] | **S** `PreToolUse` on `apply_patch`, deny; patch text in `tool_input.command` [X1] | **S** `preToolUse` deny on `Write` [U1]; `tool_input` is `{file_path, content}` with the full new file text, and `Delete` is `{file_path}` (probed, default model only) |
| Pre-shell hook | **S** `PreToolUse` on `Bash` (and `PowerShell`) [C1] | **S** `PreToolUse` on `Bash` [X1]; reported as `Bash` even when the command is PowerShell (probed) | **S** `beforeShellExecution` allow/deny/ask [U1] |
| Post-tool hook (audit) | **S** `PostToolUse` [C1] | **S** `PostToolUse`; hosted tools such as web search are not covered [X1] | **S** `postToolUse`, `afterFileEdit`, `afterShellExecution` [U1] |
| Session-end hook | **S** `SessionEnd` (short time budget) [C1] | **P** `SessionEnd` main thread only; 1 to 3 second limit [X1] | **P** `sessionEnd` fire-and-forget; not in cloud agents [U1] |
| "Ask the human" from a hook | **S** `permissionDecision: "ask"` [C1] | **N** in `PreToolUse` ("ask" fails and the call continues); `PermissionRequest` can only allow or deny when Codex was already going to ask [X1] | **P** "ask" works in `beforeShellExecution` and `beforeMCPExecution`; not enforced in `preToolUse` [U1] |
| Hook knows which agent made the call | **S** `agent_id`, `agent_type` present in tool hooks inside a sub-agent [C1] | **S** (probed, overrides the docs [X1]): tool hooks inside a sub-agent carry top-level `agent_id` and `agent_type`; main-thread calls carry neither; `session_id` is the parent's | **N** per docs: `preToolUse` carries no sub-agent id; only `subagentStart`/`subagentStop` carry the sub-agent type [U1] |
| Sub-agent delegation | **S** `Agent` tool; sub-agents nest up to 3 layers by default; off when `Agent` is not in a sub-agent's `tools` or the depth variable is 1 [C2] | **S** explicit spawn only; `max_depth` accepted by 0.153.4 but no longer documented (D-38) [X2]; spawn tool is `collaborationspawn_agent`, agent name in `tool_input.agent_type` (probed) | **S** Task tool; direct sub-agents can launch one more level [U2] |
| Per-agent model choice | **S** `model` field (alias or full ID) plus a per-invocation override [C2] | **S** `model`, `model_reasoning_effort` [X2] | **S** `model` field; falls back if plan or admin blocks the model [U2] |
| Custom commands | **S** skills and slash commands; `UserPromptExpansion` covers user-typed commands. File layout to confirm at build [C1] | **P** custom prompts deprecated and home-folder only; skills replace them [X3] | **U** official page not checked; third-party sources describe `.cursor/commands/*.md` [U4]. Not needed (P-09) |
| Project rules file | **S** `CLAUDE.md`, `.claude/rules/*.md` [C1] | **S** `AGENTS.md`, joined root to working folder, 32 KiB combined by default [X5] | **S** `.cursor/rules/*.mdc`, `AGENTS.md` [U3] |
| Sandbox or approval modes | **S** permission modes: default, plan, acceptEdits, auto, dontAsk, bypassPermissions [C1] | **S** `sandbox_mode` per agent; sub-agents inherit the parent's sandbox and approvals [X2] | **U** shell hook input has a `sandbox` flag; modes not shown in checked pages [U1]. Not needed: no agent is read-only (D-41) |
| Extra: parallel sub-agent cap | **S** `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` (default 20, set to 10) [C2] | **S** `agents.max_concurrent_threads_per_session`, set to 10; `max_threads` is a legacy alias (D-38) [X2] | **U** no documented cap found. Not needed: engine lease cap |
| Extra: hook failure behaviour | Only exit 2 or JSON deny blocks; exit 1 and timeouts do not [C1] | Unsupported reply fields fail the hook and the call continues; hooks need trust review [X1]; a changed hook loses its trust and is skipped silently (probed) | Fail-open by default; `failClosed: true` blocks on failure; bad JSON blocks permission hooks [U1] |

### 5.1 Agent identity in hooks (open question kept, answered per tool)

**Does the hook know which agent made the call?**

- **Claude Code: Yes.** Per-agent path rules (for example "the Architect may write only under `.flamin/`") are **Hard**.
- **Codex: Yes (probed; the docs say No).** Every tool call a sub-agent makes fires `PreToolUse` with top-level `agent_id` and `agent_type`. A call without these fields comes from the main thread, where the Orchestrator runs. Per-agent path rules are **Hard** on Codex (P-16). Caveat: this is a probe result on Codex 0.153.4, not documented behaviour. If a later Codex version drops these fields, the engine treats the call as unidentified and Codex falls back to the Cursor row below.
- **Cursor: No (per current docs; not probed for sub-agents).** Per-agent path rules are **Soft**. Fallback: caller-agnostic Hard rules (locks, boundaries, phase, state-file protection) still apply to every call, plus the pre-commit backstop, plus the Heuristic "active agent lease" below.

**Active agent lease (Heuristic).** Before delegating, the Orchestrator runs `flamin develop <module>` (or the matching step command), which records which agent holds which module. A pre-write hook can then check "is this path inside a module that has a lease?" This works well when one sub-agent runs at a time. With several parallel sub-agents on different modules it still stops writes to modules with no lease, but it cannot tell two leased agents apart.

### 5.2 What the gaps mean in practice

- Codex and Cursor lack "ask" in the general pre-tool hook. Approval Gates on those tools always **deny first**, then the human approves through the engine (Section 15.3).
- Cursor's pre-write payload is not documented, but the probe showed it is readable (§5.4): `Write` carries the full new file text, including for a one-line edit. As P-10 planned, lock and boundary checks for `Write` move to `preToolUse` and become **Hard**. The payload is only proven with the default model (`grok-4.6`), so the P-10 workaround stays as a backstop: `afterFileEdit` reports each edit as old and new text, and on a lock or boundary breach made through any other tool the engine **reverses the edit at once**, logs it, and tells the agent why. The pre-commit backstop still applies.
- Cursor allows one extra level of nesting. The `subagentStart` hook denies a launch when `parent_conversation_id` belongs to a known sub-agent. This is **Heuristic**, because it depends on the engine having seen that sub-agent start.

### 5.3 Handling the Unknown cells

| Unknown cell | Impact if it stays Unknown | Action |
|---|---|---|
| Cursor pre-write payload | Resolved for the default model: `Write` has full content (§5.4) | Hard in `preToolUse` for `Write`; auto-revert stays for any other edit tool (§5.2) |
| Cursor edit tools on other models | Medium: another model may use an edit tool with another name or a partial payload | `preToolUse` with no matcher treats unknown tools with a `file_path` as writes (P-15); probe again when a plan with model choice is available |
| Cursor model IDs | Resolved | All three IDs confirmed on their Cursor model pages (§6.4) |
| Cursor custom commands | Low | Not needed (P-09) |
| Cursor sandbox modes | Low | Not needed: no agent is read-only (D-41); `beforeShellExecution` covers shell |
| Cursor parallel cap | Low | Not needed: the engine lease cap of 10 is the control (P-08) |

Rule for any future Unknown: the build adds a logging-only probe hook, records the real payload in VERIFICATION.md, and keeps the fallback until the probe proves the feature.

### 5.4 Live probe results (2026-09-24, Windows 11)

Four open facts were proven by listen-only probe hooks in a throwaway folder, not by reading docs. Each hook recorded its input and always allowed the call. Versions: Claude Code 2.1.228, Codex 0.153.4 (the copy bundled with the ChatGPT/Codex app), Cursor 3.21.18 (default model `grok-4.6`), Python 3.13.3. None of the probes tested a deny reply; blocking still rests on the docs.

**Cursor**

| # | Finding | Result |
|---|---|---|
| 1 | `preToolUse` sends `tool_name` and `tool_input`. `Write` has `{file_path, content}`, where `content` is the full new file text before the write. `Delete` has `{file_path}` only. A one-line edit to a 30-line file also arrived as a full `Write`. Tools seen: `Read`, `Write`, `Delete`. | Proven, default model only |
| 2 | Other models may use a different edit tool name or a partial payload. Not tested: model choice needs a paid plan. | Unverified |
| 3 | `afterFileEdit` sends `{file_path, edits:[{old_string, new_string}]}` after the write, as a small diff even when the agent sent a full `Write`. | Proven |
| 4 | Every payload includes `user_email`, plus `conversation_id`, `session_id`, `generation_id`, `model`, `cursor_version`, `workspace_roots`, `transcript_path`. | Proven |
| 5 | On Windows, hook commands run through `powershell.exe` from the workspace root. `python .cursor/x.py`, `python ./.cursor/x.py`, `.cursor\x.cmd` and `& python .cursor/x.py` (PowerShell-only syntax) all ran. | Proven |
| 6 | Several hooks on the same event started within 20 ms of each other and logged out of order. | Proven |

**Codex**

| # | Finding | Result |
|---|---|---|
| 7 | A spawn arrives at `PreToolUse` as `tool_name: "collaborationspawn_agent"` with `tool_input.{task_name, agent_type, message}`. The custom agent name is in `agent_type`. `message` is encrypted. Waiting for a sub-agent is a separate tool, `collaborationwait_agent`, with `{timeout_ms}`. | Proven |
| 8 | `SubagentStart` carries top-level `agent_type` and `agent_id`. | Proven |
| 9 | A sub-agent's own tool calls fire `PreToolUse` with top-level `agent_id` and `agent_type`; main-thread calls have neither. The shell tool is reported as `Bash` even for PowerShell commands. | Proven |
| 10 | Matchers are regular expressions matched against the whole tool name: `collaborationspawn_agent`, `.*agent.*` and `Bash` matched; `Agent` and `spawn_agent` did not. Upper/lower case handling not tested; flamin writes exact names. | Proven |
| 11 | On Windows Codex runs `command_windows`, not `command`, through `powershell.exe`. | Proven |
| 12 | Trust lives in `~/.codex/config.toml` under `[hooks.state.'<file>:<event>:<group>:<index>']` with a `trusted_hash` (sha256). After `hooks.json` changed, no hook ran and no warning was shown until the hooks were trusted again. | Proven |
| 13 | `permission_mode` is `default` in an interactive session and `bypassPermissions` under `codex exec`. | Proven |
| 14 | The bundled `codex.exe` in `~/.codex/.sandbox-bin` lacks `codex-code-mode-host.exe`, so its tool calls fail; the copy in `~/.codex/plugins/.plugin-appserver` works. | Proven (machine setup) |

**Claude Code**

| # | Finding | Result |
|---|---|---|
| 15 | An agent file with `model: claude-haiku-4-5-20251001` ran on exactly that model (per the `model` field of every sub-agent API response), with no substitution warning. `model: haiku` gave the same model. | Proven |

**Also checked:** in PowerShell, a bare `flamin.cmd` in the current folder fails with "not recognized"; `.\flamin.cmd` runs. Because Cursor and Codex both use PowerShell on Windows, their Windows hook commands use `.\flamin.cmd` (P-17).

---

## 6. Agents

### 6.1 The eight agents

Agents declare a model **tier**, never a model name.

| Agent | Tier | Job | Tools (neutral) | May write | Must never |
|---|---|---|---|---|---|
| **Orchestrator** | Deep | The only agent that talks to the human. Parses plain language, clarifies, shows the three-line preview, delegates, tracks state through the engine, writes handoffs. | read, search, shell (engine commands), delegate | `.flamin/sessions/` (through `flamin handoff`) | Write code or specs. |
| **Business** | Deep | Product vision in plain words: users, actors, journeys, features, priorities, what "good" looks like. Runs intake. | read, search, write | `.flamin/business/`, `.flamin/decisions/assumptions.md` | Write technical specs. |
| **Analyst** | Balanced | Turns agreed business input into testable requirements, acceptance criteria, edge cases and non-functional needs. | read, search, write | `.flamin/analysis/` | Touch design or code. |
| **Architect** | Deep | Stack profile, module boundaries, data model, aggregates, contracts, baseline owner, architecture review. | read, search, write | `.flamin/technical/`, `.flamin/design/` (model part), `.flamin/decisions/` | Edit product code. |
| **Planner** | Balanced | Splits work into batches of 5 or fewer interfaces, in dependency order. | read, search, write | `.flamin/analysis/<slug>/plan.md` | Edit anything but plans. |
| **Designer** | Balanced | Per-feature design specs; runs `flamin lint`; asks the engine to generate. | read, search, write, shell (engine only) | `.flamin/design/<module>/` | Hand-write business logic. |
| **Developer** | Balanced, Deep for complex tasks | Fills extension points, wires contracts, fixes bugs. | read, search, edit, write, shell | Extension points and open files inside its leased module | Edit locked code or cross module boundaries. |
| **Tester** | Balanced | Writes and runs tests, reports results. | read, search, write, shell | Test paths from the stack profile | Edit production code. |

Strength of each "may write" rule:

- Caller-agnostic parts (locked files, boundaries, phase order, state files) are **Hard** on every tool with a pre-write hook.
- Per-agent path limits are **Hard** on Claude Code and Codex, **Soft** on Cursor (Section 5.1).
- Tool allowlists are **Hard** on Claude Code. On Codex no agent is read-only (D-41); instead the hook denies every shell call from an agent whose neutral tool list has no `shell` (Business, Analyst, Architect, Planner), using `agent_type` (**Hard**). On Cursor the same rule is **Soft**, because Cursor hooks carry no agent identity. Finer limits are **Soft**.
- The Tester's "no production edits" is **Hard** on Claude Code (no `Edit` tool, `Write` limited to test paths by the hook) and on Codex (the hook limits the Tester's writes to test paths using `agent_type`), **Soft** on Cursor, backed by the pre-commit check that production files changed in a Tester step only inside extension points (Heuristic).

**Developer on Deep.** The Orchestrator chooses Deep for a Developer task when the task spans several aggregates, touches concurrency, or failed once on Balanced. It states the reason in the three-line preview and the engine logs it. (Soft choice, logged.)

### 6.2 Work order

Work flows in sequence: **Business → Analyst → Architect → Planner → Designer → Developer → Tester**. Parallel work happens only inside one step, for example two Developers on two different modules. Parallel work never spans two steps. One planned exception: at Step 0 the Architect recommends the stack right after intake, before the Analyst starts (§8.2). The launch gate reads the allowed agents per step from the table in Section 9, so this exception is explicit, not a gap. (Hard for agent launches where the tool can block a launch: Claude Code `PreToolUse` on `Agent`, Codex `PreToolUse` on `collaborationspawn_agent`, Cursor `subagentStart`. The engine checks the requested agent type against the current phase and step. On Codex it reads `tool_input.agent_type`, and `SubagentStart` gives a second check on the top-level `agent_type` (probed, §5.4).)

### 6.3 Delegation limits

- **Depth 1.** Only the Orchestrator delegates. Hard on Claude Code (no `Agent` tool for sub-agents, plus spawn depth set to 1) and Codex (the engine launch gate denies a spawn from a caller that carries `agent_type`; `max_depth = 1` is extra protection, D-38). Heuristic on Cursor (Section 5.2).
- **Up to 10 sub-agents in parallel.** Hard on Claude Code (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS = 10`) and Codex (`max_concurrent_threads_per_session = 10`, D-38). On Cursor there is no documented cap, so the engine's refusal of an eleventh module lease is the control (Hard at engine level).
- **One agent per module at a time.** Hard at engine level through the module lease. Heuristic at file level (Section 5.1).
- **Target of 10 steps or fewer per agent per task, on average.** Soft. `flamin audit --stats` measures it from the audit log. No hard cap on tools used.

### 6.4 Model tiers and `models.json`

```json
{
  "claude": { "Deep": "claude-opus-5-5", "Balanced": "claude-sonnet-5", "Fast": "claude-haiku-4-5-20251001" },
  "codex":  { "Deep": "gpt-6-astra",     "Balanced": "gpt-6-sol",       "Fast": "gpt-6-luna" },
  "cursor": { "Deep": "claude-opus-5-5", "Balanced": "claude-sonnet-5", "Fast": "composer-2.5" }
}
```

- Claude values: the Claude Code docs show `claude-opus-5-5` and `claude-sonnet-5` as valid full IDs for the agent `model` field, which accepts the same values as `--model`. `claude-haiku-4-5-20251001` is the current API string for Haiku 4.5, confirmed by a live launch on Claude Code 2.1.228 with no substitution warning (§5.4).
- Codex values come from the current Codex models page: Astra is described as the most capable, Sol as the model for complex coding, Luna as the efficient one. Availability depends on plan and sign-in method. GPT-5.5 retires from Codex with ChatGPT sign-in on 2026-10-14, so it is not used.
- Cursor values (P-11): Claude Opus 5.5 for Deep (Cursor's current Opus), Claude Sonnet 5 for Balanced (same family as the Claude adapter, so agent behaviour stays close across tools), Composer 2.5 for Fast (Cursor's own fast, low-cost model). The IDs `claude-opus-5-5`, `claude-sonnet-5` and `composer-2.5` are each stated on their Cursor model page. Plan limit: the two Claude models draw from Cursor's "Other Models" pool, which the India-only Start plan does not include; on that plan Cursor falls back and the `subagentStart.subagent_model` check warns.
- **One session uses one tool.** Different sessions or people may use different tools on the same product, because all state lives in `.flamin/`.
- **Model mismatch warning.** All three tools can set a model per agent, but Cursor falls back silently when a plan or admin blocks a model, and a Codex bug report describes a similar override (not confirmed in official docs). Where a hook reports the real model (Cursor `subagentStart.subagent_model`, Codex hook `model` field), the engine compares it with the tier and makes the Orchestrator warn before a Deep-tier step runs on a lower model. On Claude Code the sub-agent's model is not reported to hooks, so the warning there is **Soft**.

### 6.5 Tool-neutral agent format

Each agent lives in `kit/agents/<agent>.json`:

```json
{
  "name": "developer",
  "tier": "Balanced",
  "deep_when": "multi-aggregate change, concurrency, or failed once on Balanced",
  "job": "Fill extension points, wire contracts, fix bugs.",
  "tools": ["read", "search", "edit", "write", "shell"],
  "may_write": ["<module>/**/extension points", "<module>/**/open files"],
  "must_never": ["edit locked code", "cross module boundaries"],
  "read_only": false,
  "prompt_file": "developer.md"
}
```

Adapters render this into each tool's native file. The neutral tool names map to native ones in the adapter (for example `edit` → Claude `Edit`, Codex `apply_patch`, Cursor `Write`).

### 6.6 Handoff at 80% context

- The trigger is **Soft**: agents are told to write a handoff when context use nears 80%. Tools report context use in different ways (Claude Code status line and `PreCompact`, Cursor `preCompact` with `context_usage_percent`, Codex `/status`). A `PreCompact` hook gives a late **Heuristic** trigger: it records the event and reminds the Orchestrator to write the handoff first.
- **Step-boundary checkpoint (P-06, Hard).** Every engine step command appends an entry to `.flamin/sessions/ledger.md` (step, module, files changed, decisions). If a session runs out of context before a handoff, at most the current step's work is lost, and `flamin resume` rebuilds the State Ledger from this file.
- The handoff file `.flamin/sessions/<date>-<session>.md` must have three sections:

```markdown
## Objective
What the task is and what "done" means.

## State Ledger
- Completed: Step 6 booking (batch 1 of 2)
- Current: Step 7 booking batch 2
- Pending: Steps 8 to 10 booking
- Decisions: D-14 seat hold time is 10 minutes (agreed 2026-10-03)
- Open questions: refund rule for partial shows

## Artifact References
- Changed: .flamin/design/booking/hold-seat.toml
- Must read: .flamin/analysis/booking/plan.md, .flamin/business/rules.md
```

- `flamin handoff --validate` rejects a file missing any of the three headings or with an empty section. (Hard.)

---

## 7. Natural language first, CLI underneath

Anyone can drive flamin in plain words. The CLI is the exact, repeatable engine underneath and the fallback when language is unclear.

### 7.1 Three-line preview

Before acting, the Orchestrator shows:

```
1. Understood: add a "cancel booking" feature to the booking module.
2. Planned:    Analyst writes requirements, then `flamin cr-new "cancel booking"`.
3. Because:    product is released at v1; new work goes into v2 as a CR (Section 16).
```

(Soft: this is an instruction to the Orchestrator. Every engine command it then runs is logged, so a missing preview is visible in the audit log.)

### 7.2 Clarification rules

- Clarify as early as possible, and always before a spec is locked. This matters most during business requirement gathering.
- No limit on the number of questions or assumptions.
- Every assumption needs an explicit "agreed" from the human, even after a warning. Silence is not agreement.
- Mandatory intake items (Section 8) halt the process until clear.
- Every question, answer and assumption goes into `.flamin/decisions/assumptions.md`, with an id, the exact words of the answer, and a status (open, agreed, rejected).

Strength:

- The questions themselves are **Soft** (a model decides what is unclear).
- **Hard part:** `flamin baseline-review`, `flamin design` and `flamin cr-approve` refuse to run while any assumption linked to that scope is still `open`. The engine accepts "agreed" only through `flamin approve <assumption id>`, which follows the Approval Gate path (Section 15.3). The status is stored in `.flamin/approvals.json`, never read from `assumptions.md` (P-20).
- Natural language understanding is Soft, so it can never bypass a Hard gate. A plain-language request that maps to a gated command still goes through the gate.

---

## 8. Product intake and runtime recommendation

### 8.1 Intake (Step 0, Business agent)

The first questions are always: product name, purpose, and who will use it.

Mandatory items. The process **halts** until each is clear:

1. Product name
2. Purpose
3. Target users and actors
4. Core user journeys
5. Must-have features for the first release
6. Target platforms (web, desktop, iOS, Android, backend)
7. Data sensitivity (personal data, payment data)
8. Success criteria

`flamin intake --check` lists missing items. Phase 1 cannot start while any item is missing. (Hard: `check-phase` blocks Step 1 commands and Step 1 agent launches.)

### 8.2 Runtime and stack recommendation (Architect)

After intake, the Architect recommends the product runtime and stack profile(s):

- Explains the choice in plain words (for example: "Python with FastAPI for the backend, because the team knows Python and the product is mostly forms and reports").
- States up front if iOS is a target: iOS builds need macOS with Xcode or a cloud build service.
- Records the recommendation in `.flamin/decisions/stack.md`.
- Waits for human approval through the gate. On "yes", the engine writes `stack.json` and copies the profile(s) into `.flamin/stacks/`. (Hard.)
- If no profile fits, the Architect proposes a new one. A new profile is an Approval Gate item.

---

## 9. The flow: three phases, thirteen steps

Think of it like building a house: agree what the family needs (intake), draw the plan (Phase 1), build the rooms in parallel (Phase 2), then inspect and hand over the keys (Phase 3).

| Step | Name | Agent | Engine command | Gate |
|---|---|---|---|---|
| 0 | Intake and stack choice | Business, then Architect | `intake`, stack approval | Mandatory items; stack approval |
| **Phase 1** | **System modeling (sequential)** | | | |
| 1 | Requirements and domain discovery | Business, then Analyst | `analyze` | Open assumptions |
| 2 | Module division and boundaries | Architect | `model --step 2` | |
| 3 | Data model | Architect | `model --step 3` | |
| 4 | Aggregates and invariants | Architect | `model --step 4` | |
| 5 | Baseline review | Architect runs checklist; human confirms | `baseline-review` | Human "agreed" |
| **Phase 2** | **Module development (parallel per module)** | | | |
| 6 | Interface list and batches | Planner | `plan` | Baseline confirmed |
| 7 | Detailed design, then generation | Designer | `design`, `generate` | Step 6 done for module |
| 8 | Business logic in extension points | Developer | `develop` | Step 7 done for module |
| 9 | Cross-module wiring through contracts | Developer | `wire` | Step 8 done for module |
| 10 | Module self-test | Tester | `module-done` | Step 9 done for module |
| **Phase 3** | **Integration and release (sequential)** | | | |
| 11 | Integration test | Tester | `integration-test` | All modules `self_test: pass` |
| 12 | Architecture review | Architect | `architecture-review` | Step 11 pass |
| 13 | Release proposal and decision | Orchestrator proposes; human decides | `release` | Release gate |

Order enforcement: **Hard** on engine commands (each checks its prerequisites) and on agent launches where the tool can block a launch (Section 6.2). File writes that belong to a later step (for example product code before the baseline) are blocked by `check-phase` in the pre-write hook (Hard where that hook exists) and in the pre-commit backstop.

### 9.1 Baseline review checklist (Step 5)

- Are module boundaries clear, with no overlapping jobs?
- Does the data model cover every agreed journey?
- Are cross-module dependencies free of cycles? (The engine checks cycles in `modules.json`. Hard.)
- Are aggregate boundaries and transaction scopes right?
- Are enums and value objects complete?
- Is every assumption in scope agreed? (Hard.)

On pass and human "agreed", `baseline.confirmed = true` and `phase = 2`.

### 9.2 Baseline amend (the logged escape hatch)

A real gap found mid-build must not be hidden, and must not stop the whole project either. `flamin baseline-amend "<reason>"`:

1. Is an Approval Gate item (Section 15.3).
2. Appends the reason, the approver and the time to `.flamin/decisions/baseline-amendments.md` before anything reopens.
3. Opens a short write window for the Architect on the data model specs, limited to the modules named in the request.
4. Closes the window when the Architect runs `flamin model --done`, then re-runs the Step 5 cycle check.

The baseline stays "confirmed" with an amendment count. Every change is visible and has a reason. (Hard for the gate and the log; the window scope is Hard on Claude Code and Codex and Heuristic on Cursor, because it relies on agent identity.)

### 9.3 Module self-test checklist (Step 10)

- All interfaces pass normal-case tests.
- Bad input is rejected with the standard error envelope.
- Cross-module calls work, or use recorded mocks until the other module is done.
- No locked code was edited (`check-lock` on the module. Hard).
- No direct cross-module imports (`check-boundary` on the module. Heuristic).

---

## 10. Stack-neutral architecture

These ideas work in any language. The stack profile gives them concrete folder names.

### 10.1 One-way layer dependency (principle)

Outer layers call inner layers, never the reverse. Like a restaurant: the waiter talks to the kitchen, the kitchen uses the pantry, but the pantry never calls the waiter.

Neutral default layers, outer to inner:

| Layer | Job | May use |
|---|---|---|
| `interface` | Entry points: HTTP handlers, screens, CLI commands. Checks input shape, calls application, wraps the response. | application, shared, contracts |
| `application` | Use cases and write flows. Owns transactions. Implements the module's contracts. | assembly, data, shared, contracts |
| `assembly` | Builds internal transfer objects from storage models and runs post-processing. | data, shared |
| `data` | Storage models, queries, persistence. | shared |
| `shared` | Module enums, constants, helpers. | nothing inside the module |
| `contracts` | Public interfaces other modules may call, plus the objects they exchange. | shared |

Layer names and count come from the profile. The boundary check reads the "may use" table from the profile. (Heuristic: it is an import check, Section 13.)

### 10.2 Layers are code layers, not user roles

Layers describe **where code lives**. They do not describe **who uses the product**. Actors such as customer, staff and super admin are listed in `.flamin/business/actors.md` with a permissions table:

| Actor | Can | Cannot | Sees |
|---|---|---|---|
| Customer | book, cancel own booking | see other customers | own bookings |
| Staff | check in any booking | change prices | bookings for own cinema |
| Super admin | everything | nothing | everything |

Every actor's request passes through the **same** layers. The permission check lives in the application layer through a shared extension point (`checkPermission`), so it is written once and tested once.

### 10.3 "No layers" profiles

A profile may declare `"layers": []` for small products (scripts, small tools). For that project the layer and boundary checks switch off. Lock levels and phase checks still apply.

### 10.4 Read path and write path kept separate

- **Write path:** interface → application (use case) → aggregate root → data. One write touches one aggregate.
- **Read path:** interface → application (query) → data query → assembly → post-processing extension point → view output.

Each profile picks its own tools for each path. A profile may use the same library for both.

### 10.5 Aggregate root with an invariant check

Each aggregate has one root object. Before every write, the root's `validateAggregate` extension point runs (for example "order total equals the sum of its lines"). Business rules live there once, not repeated in handlers. The generator wires the call. (Hard that the call exists in generated base code; the rule content is written by the Developer.)

### 10.6 Data object roles

| Role | Used for | Crosses the interface layer? |
|---|---|---|
| Storage model | Maps to a table or collection | No |
| Internal transfer object | Moves data between assembly and application | No |
| Write input | Parameters of a write use case | Yes (in) |
| Query input | Parameters of a read query | Yes (in) |
| View output | What the caller receives | Yes (out) |

The interface layer accepts only write input, query input, plain values and enums, and returns only view output inside the standard envelope. (Heuristic check in `flamin-lint` on specs.)

### 10.7 Locked base code plus open extension code

Generated code comes in two parts: a base part that only the generator may change, and an extension part where people and agents add logic at named points. Section 12 defines the lock levels.

### 10.8 Contracts between modules

- Cross-module calls go **only** through the other module's `contracts` layer.
- Same-module calls are direct.
- `modules.json` maps each module to its contract files.
- If the other module is not ready, the caller uses a recorded mock and switches over in Step 9.

### 10.9 Standard response envelope

Every interface response uses the same shape:

```json
{ "code": 0, "message": "ok", "data": { } }
```

Errors use a non-zero `code` and a human-readable `message`. Profiles generate a helper so handlers never build the envelope by hand.

---

## 11. Stack profiles

### 11.1 What a profile defines

Each profile is a folder `kit/stacks/<profile>/` (copied into `.flamin/stacks/<profile>/` on approval) with `profile.json` and `templates/`:

```json
{
  "name": "python-fastapi",
  "targets": ["backend"],
  "layers": ["interface", "application", "assembly", "data", "shared", "contracts"],
  "layer_dirs": { "interface": "api", "application": "services", "assembly": "assemblers",
                  "data": "repository", "shared": "common", "contracts": "contracts" },
  "may_use": { "interface": ["application", "shared", "contracts"] },
  "module_root": "modules/<module>/",
  "extensions": [".py"],
  "lock_comment": "#",
  "import_patterns": ["^\\s*from\\s+modules\\.(\\w+)\\.(\\w+)", "^\\s*import\\s+modules\\.(\\w+)\\.(\\w+)"],
  "test_patterns": ["tests/**/test_*.py"],
  "commands": { "build": "python -m compileall modules", "test": "python -m pytest -q" },
  "templates": "templates/"
}
```

(`may_use` is shortened here. The full table lists every layer.)

### 11.2 Shipped profiles

| Profile | Targets | Notes |
|---|---|---|
| `java-spring` | backend | Folder names: `entrance` (interface), `service` (application), `manager` (assembly), `persist` (data), `common` (shared), `contracts`. |
| `python-fastapi` | backend | Folders: `api`, `services`, `assemblers`, `repository`, `common`, `contracts`. |
| `typescript-node` | backend | **Express.** See 11.3. |
| `javascript-node` | backend | Express, same template shape as `typescript-node` without types. |
| `typescript-react` | web | Folders: `pages` (interface), `features` (application), `adapters` (assembly), `api-client` (data), `shared`, `contracts`. |
| `react-native` | iOS, Android | **React Native with TypeScript.** See 11.3. |
| `minimal` | any | `"layers": []`. Lock levels and phases only. |

### 11.3 Two picks and why

- **Express over NestJS for `typescript-node`.** Express is small and has few dependencies, so generated code stays short and predictable. It also lets `typescript-node` and `javascript-node` share one template shape. NestJS adds its own module system and dependency injection, which overlaps with flamin's own module and contract rules and makes templates larger. A NestJS profile can be added later with the steps in 11.6.
- **React Native over Flutter for mobile.** React Native uses TypeScript, like `typescript-react` and `typescript-node`. That means one lock-marker syntax, one import pattern and one test runner family across web, backend and mobile. Flutter would add Dart and a second set of templates and checks.

### 11.4 Mobile rules

- iOS builds need macOS with Xcode, or a cloud build service. When iOS is a target, the Architect says so at Step 0, before the stack is approved.
- A cloud build service is an external, often paid, API call, so it is an Approval Gate item.
- App store submission is an Approval Gate item.
- On a machine that cannot build for a device, flamin still generates, lints and unit-tests the code, and says plainly that device and store builds were not run.

### 11.5 More than one profile

A product may use several profiles, for example `python-fastapi` + `typescript-react` + `react-native`. `stack.json` lists each profile with its root folder. Checks pick the profile by file path.

### 11.6 Adding a new stack profile

For native iOS (Swift), native Android (Kotlin), Go, C#, data pipelines and others:

1. Copy `minimal` or the nearest profile into `kit/stacks/<new-name>/`.
2. Fill `profile.json`: layers (or none), folder names, file extensions, lock comment syntax, import patterns, test patterns, build and test commands, targets.
3. Write templates for each layer and each object role that the profile needs.
4. Add lint rules if the language has special cases (for example Go packages or C# namespaces).
5. Add unit tests: one lock-marker test, one import-pattern test, one generate-then-test example.
6. For a product, the Architect proposes it and the human approves it through the gate.

### 11.7 Lock marker examples

Java or TypeScript (`//` comments):

```java
// FLAMIN:LOCK FULLY_LOCKED
public abstract class BaseMemberService { /* generated */ }
```

```ts
// FLAMIN:LOCK STRUCTURE_LOCKED
export class MemberService extends BaseMemberService {
  async registerMember(input: RegisterMemberInput) {
    // FLAMIN:EXTENSION validateAggregate
    // hand-written logic goes here
    // FLAMIN:EXTENSION:END
  }
}
```

Python (`#` comments):

```python
# FLAMIN:LOCK STRUCTURE_LOCKED
class MemberService(BaseMemberService):
    def register_member(self, data: RegisterMemberInput):
        # FLAMIN:EXTENSION validateAggregate
        # hand-written logic goes here
        # FLAMIN:EXTENSION:END
        return super().register_member_base(data)
```

---

## 12. Lock levels

| Level | Who may change it | Typical content |
|---|---|---|
| `FULLY_LOCKED` | Only `flamin generate` | Base classes, generated converters, input and output definitions |
| `STRUCTURE_LOCKED` | Generator for the structure; anyone for the body between `FLAMIN:EXTENSION` and `FLAMIN:EXTENSION:END` | Service shells, handler shells, aggregate roots |
| Open (no marker) | Anyone allowed by other rules | Hand-written helpers, tests |

**Two sources, one truth.** Inline markers live in the files, where people and agents look. `locks.json` is a fast cache of those markers, rebuilt by `flamin rebuild-locks` after every generation. It also stores the spec hash and template hash used to make each file (for drift checks, Section 14).

**How `check-lock` decides (improved from a plain line-range check).** The engine applies the proposed change to the current file in memory, then compares before and after:

1. File not in `locks.json`: allow.
2. `FULLY_LOCKED`: deny, and name the nearest extension point in a sibling file if one exists.
3. `STRUCTURE_LOCKED`: allow only if every changed line is inside one extension body and every marker line is unchanged. Otherwise deny and show which lines are outside.
4. **Regeneration bypass:** while `.flamin/tmp/regenerating.lock` exists (created and removed by `flamin generate` itself), rules 2 and 3 are skipped for the files the generator writes. This is the only way past a lock.

Works on: Claude Code `Edit` and `Write` payloads, Codex `apply_patch` text (the engine parses the patch), full-file writes. Cursor `Write` is a full-file write with the complete new text (probed with the default model, §5.4); other Cursor edit tools fall back to the auto-revert in Section 5.2.

Strength: **Hard** where a pre-write hook with a readable payload exists, and always at the pre-commit backstop. It is a text check, not a code parser, so it can over-block an edit that includes extra context lines. The fix is a smaller edit. Shell commands that write files (`sed -i`, `>`, `cp`, `mv`, `tee`) are checked by a **Heuristic** pattern scan in the pre-shell hook, and fully by the backstop.

---

## 13. Module boundaries: three-layer defence

Like airport security: a quick bag check at the door, a scanner at the gate, and a final check before boarding. No single layer is trusted alone.

1. **Boundary map (`modules.json`).** Maintained by the Architect through engine commands. Lists each module and its contract files.
   ```json
   { "booking": { "contracts": ["modules/booking/contracts/"] },
     "catalog": { "contracts": ["modules/catalog/contracts/"] } }
   ```
2. **Pre-write check (`check-boundary`).** Runs in the pre-write hook and the pre-commit backstop. It finds the module of the target file from its path, scans the new content with the profile's import patterns, and allows: same module, shared code, and the other module's `contracts` path. Any other in-project module import is denied, and the message names the right contract file. External packages are always allowed. Also checks the layer "may use" table. **Heuristic:** it catches direct imports, which is the common mistake. It cannot see reflection, dynamic paths or clever routing.
3. **Architecture review (Step 12).** The Architect runs `flamin architecture-review`, which scans the **whole** product, not just one edit. It catches what the per-edit check missed, such as files written by shell commands. `flamin release` is blocked until the review passes. (Hard gate on the result; the scan itself is Heuristic.)

A language-level architecture test (for example an import-rule test in the product's test suite) is recommended as a fourth layer inside Step 10. It runs after code exists, so it complements the pre-write check but cannot replace it.

---

## 14. Design specs, `flamin-generate` and `flamin-lint`

### 14.1 Spec files

One spec per feature: `.flamin/design/<module>/<name>.toml` (TOML: easy for people to read, read by the standard library `tomllib`; P-05).

```toml
entity = "Member"

[api]
method = "POST"
path = "/api/member/register"
input = [
  { name = "phone", type = "string", required = true, unique = true },
  { name = "password", type = "string", required = true },
]

[write_plan]
aggregate_root = "Member"
invariants = ["phone is unique at registration"]

[[extension_points]]
name = "validateAggregate"
hint = "check phone is unique, hash password"

[[extension_points]]
name = "postProcessData"
hint = "none for this endpoint"
```

Specs are the easiest place to read the structure of generated code. Reading a spec is faster than reading the base classes it produced.

### 14.2 `flamin-generate` (`flamin generate <spec>`)

1. Creates `.flamin/tmp/regenerating.lock`.
2. Reads the spec and the profile templates (`string.Template` plus engine-side loops for lists).
3. Writes `FULLY_LOCKED` files fresh, every time.
4. Writes `STRUCTURE_LOCKED` shells. Existing extension bodies are **kept**: the engine copies each named body from the old file into the new shell. A body whose name disappeared from the spec is moved to `.flamin/tmp/orphans/` and reported, never silently lost.
5. Runs `rebuild-locks`, records spec and template hashes, removes the regeneration lock.

Deterministic: the same spec and templates give byte-identical output. (Hard, unit-tested.)

### 14.3 `flamin-lint` (`flamin lint`)

Checks specs before generation: every field the write plan uses exists; extension point names match the templates; interface inputs use only allowed object roles; no module references outside its contracts. Also runs `--portability` (absolute paths, user names) and `--drift` (Section 14.4).

### 14.4 Honest list of weaknesses

1. **No live validation.** A spec is just a file until `flamin lint` runs. Nothing stops an inconsistent spec from being saved.
2. **Template-bound, not model-driven.** The generator can only produce shapes someone already wrote a template for. A new kind of field or relation needs a new template first.
3. **Drift detection is partial.** Hashes in `locks.json` show when a spec or template changed after generation (`flamin lint --drift`). They cannot prove the running code matches the business intent.
4. **Specs are only a source of truth by habit.** If people edit generated code around the specs, the specs go stale. The lock checks stop edits to locked parts, but not every misuse.
5. **Gaps in templates fall back to hand-written code.** Where no template fits, the Developer writes it by hand, which brings back the unpredictability the generator exists to avoid. Each such gap is logged as a template to add.
6. **Extension body carry-over is by name.** Renaming an extension point in a spec orphans its body (moved aside and reported, not merged).

---

## 15. AI hooks

Three hooks are built. The fourth is described for later.

### 15.1 PreToolUse: inspect or block a call before it runs

Entry: `flamin hook pretool --tool <name>` (Claude Code `PreToolUse`, Codex `PreToolUse`, Cursor `preToolUse`, `beforeShellExecution`, `beforeMCPExecution`, `subagentStart`).

It blocks:

- **Destructive commands:** recursive delete outside the project, force push, history rewrite of shared branches, `drop table`/`drop database`/`truncate`, wiping or moving `.flamin/` state files, disk format commands, and similar.
- **Secret leaks:** API keys, tokens, private keys and passwords found in file content, command lines or network call arguments. Patterns cover common key prefixes, private key headers, `password=` style assignments, and long high-entropy strings next to words like "key", "token" or "secret".
- **Agent writes to `.flamin/*.json`** (only the engine writes state).
- **Agent writes to the enforcement layer** (P-19): `kit/`, the two launchers, `.gitattributes` (D-45), `.claude/`, `.codex/`, `.cursor/`, `CLAUDE.md`, `AGENTS.md`, `.git/` and `.flamin/stacks/`. Only `flamin init`, `flamin upgrade` and an approved stack profile change write these. File-tool writes are blocked (Hard where a pre-write hook exists). Shell writes to these paths are caught by pattern (Heuristic). Backstop: `kit/MANIFEST` holds a hash of every kit file, and `check-staged` and `flamin doctor` fail on any mismatch that `flamin upgrade` did not make (Heuristic, because an edit to the engine and the manifest together could hide itself; CI on a clean runner narrows this). Reason: an agent that can edit the engine or a hook config can switch every other check off. Exception: in master-kit maintenance mode this block is lifted for kit files only (D-46).
- **Agent runs of human-only commands** (`flamin approve`, `cr-approve`, release approval, `kit-maintenance`), except through the ask path in 15.3.
- **Shell calls from agents with no shell tool** (D-41): every shell call from Business, Analyst, Architect or Planner. Hard on Claude Code and Codex (agent identity in the hook), Soft on Cursor.

It runs `check-lock`, `check-boundary` and `check-phase` on file writes and agent launches.

Patterns are built-in defaults for now. Configurable policy files are deferred (15.4).

Strength: blocking is **Hard** where the tool's hook can deny (all three tools; Cursor content checks are proven for `Write` with the default model only, §5.4). Detection of destructive commands and secrets is **Heuristic** (pattern based).

### 15.2 Audit Logging: record every step

Entry: `flamin hook posttool --tool <name>` plus the engine's own commands, which log themselves.

- File: `.flamin/audit/YYYY-MM-DD.jsonl`, one JSON object per line, inside the project.
- Fields (at least):

```json
{"ts":"2026-10-03T08:15:22Z","session":"s-81f2","tool":"codex","agent":"developer",
 "action":"write","target":"modules/booking/services/hold_seat.py","decision":"deny",
 "reason":"STRUCTURE_LOCKED: lines 12-14 outside extension validateAggregate",
 "approver":null,"version":"v2"}
```

- `agent` is exact on Claude Code and on Codex (from the hook's `agent_type`; a call without it is the Orchestrator on the main thread, §5.1). On Cursor it comes from the module lease (Heuristic) and is marked `"agent_source":"lease"`, or `"unknown"` when no lease matches.
- **Redaction before writing.** The same secret patterns replace matches with `[REDACTED:<kind>]`. Personal fields the tool adds to hook input, such as Cursor's `user_email`, are dropped. (Heuristic detection, Hard that the redaction step always runs before the write.)
- Append-only, one line per write, under the same `.flamin/.lock` as state. (Hard.)
- **One line per tool call (D-44).** When one tool call reaches the engine more than once (for example Cursor also running Claude Code hook files), the engine writes one audit line. The key is `tool_use_id`, else `generation_id` plus hook event plus a hash of the tool input. Different tool calls never share a key, so no real action is dropped (D-37).
- `flamin audit` prints plain readable lines, for example: `08:15 codex developer DENY write modules/booking/... (locked lines 12-14)`. The stored file stays JSON Lines.
- **Retention.** After each append, the engine deletes the oldest daily files while total size is over 1 GB, and deletes any file older than 1 year. Whichever limit hits first wins. These deletes are logged, not gated.
- Excluded from git by default.
- Tools with no post-hook for a given action (for example hosted web search in Codex) leave no audit line for that action. This gap is listed in VERIFICATION.md at build.

### 15.3 Approval Gates: pause and ask the human

Gated by default:

- Production release
- App store submission
- Database migrations
- Deleting files (by agents)
- Calls to external or paid APIs (including cloud build services)
- Secret or credential changes
- Baseline amend
- New stack profile
- New dependencies
- CR list approval before code changes
- Stack approval at Step 0 and assumption "agreed" records (§7.2)

Not gated but logged: engine-internal deletes (audit retention, temp files, regenerated non-extension code).

**What the request shows:** the three lines from §7.1, plus the exact command or diff, plus a request id such as `R-0012`.

**How the human answers, per tool:**

| Tool | Path | Strength |
|---|---|---|
| Claude Code | The Orchestrator runs `flamin approve R-0012`. The pre-shell hook replies `ask`, so Claude Code shows its own permission prompt. The human confirms there. The engine records `approver: "human via claude prompt"`. | Hard (the tool's prompt, not the model, asks) |
| Cursor | Cursor hook input has no permission-mode field, so the hook always **denies** and tells the human to run `flamin approve R-0012` in a terminal (D-42). The engine records `approver: "human via terminal"`. | Hard (agent path blocked; human types the command) |
| Codex | "ask" is not supported in `PreToolUse`. The hook **denies** and tells the human to run `flamin approve R-0012` in a terminal. The engine records `approver: "human via terminal"`. | Hard (agent path blocked; human types the command) |
| Any tool | The human runs `flamin approve R-0012 --yes` or `--no` in a terminal. | Hard |

Rules:

- Every approval and every rejection goes to the audit log with the request id.
- **No answer means no.** A request stays pending and the gated action never runs. Pending requests show in `flamin status`.
- A repeated hook call for the same tool call reuses its open request instead of opening a second one (D-44; same key as §15.2).
- An approval covers only the exact command or diff shown. A changed diff needs a new request. (Hard: the engine stores a hash of what was approved.)
- **Where gate decisions live (P-20).** Open requests, answers, approved hashes and assumption status live only in `.flamin/approvals.json`, an engine-owned top-level state file (write-blocked for agents and checksummed, §3.2). Markdown files in `.flamin/decisions/` are readable notes. The engine never reads a gate decision from a file an agent may write. Reason: the Business agent may write `assumptions.md` and the Architect may write `.flamin/decisions/`, so a status kept there could be self-approved.
- **When the tool prompt is trusted (P-21).** The `ask` path is used only when the hook input shows an interactive permission mode. In a bypass or auto-approve mode (for example Claude Code `bypassPermissions`), in a mode the engine does not recognise, or when the mode field is missing, the hook denies and sends the human to the terminal path, as on Codex. Reason: in those modes the tool prompt may be skipped, and the agent would approve itself. Whether `ask` still reaches the human in each mode is Unknown and is probed at build. Claude Code `auto` mode stays on this deny path (D-43). The Claude Code docs say a hook's `ask` still prompts in auto mode, but public bug reports describe surfaces where it did not, and the hook input shows neither version nor surface. Relaxing this needs a new decision backed by a live interactive probe on each surface (terminal, VS Code extension, desktop).

### 15.4 PreInvocation (deferred)

A future option for enterprise-wide systems: a safety filter that checks prompts before they reach any model (for example blocking pasted customer data, or known prompt-injection text). Together with configurable policy files, it would let a company set one policy for every product and every tool. It is not built in this kit version.

---

## 16. Versions, releases and change requests

### 16.1 Version scheme

- Every product starts at **v1**, built from scratch through the full flow (intake, Phase 1, Phase 2, Phase 3).
- Each later round of change is the next version: v2, v3, and so on. This includes bug fixes found after any release.
- There are no D cycles and no major or minor versions.
- **Machine versions** for tools and app stores: `v<n>` becomes `<n>.0.0`, plus a build number that always increases (stored in `state.json`, never reused). Humans only see `v<n>`. (Hard.) The build number goes up by 1 each time `flamin release` builds a release candidate, and at no other time (P-22). Reason: app stores need a new, higher number for every upload, and a test can only check a number whose trigger is defined.

### 16.2 Each version after v1

1. The human raises a list of change requests: `flamin cr-new "<title>"` (or in plain words). A CR joins the open version until that version's CR list is approved. After approval, new CRs join the next version (P-22).
2. Business and Analyst analyse each CR: impact, affected modules, risks, questions. The result goes into `.flamin/versions/v<n>.md`.
3. The human approves the CR list through the gate (`cr-approve`) **before any code change**. (Hard: `develop` and `generate` refuse to run for a version whose CR list is not approved.)
4. Agents change the code and test it. Only the steps a CR needs are re-run for the affected modules (for example design, generate, develop, test). A CR that changes the data model needs a baseline amend (Section 9.2). `cr-approve` records, for each CR, the affected modules and the earliest step it needs. The engine resets those modules' step state from that step, so `check-phase` allows exactly that rework and nothing more (P-23).
5. Integration test and architecture review run again.
6. The human validates, then the version closes.

### 16.3 Release decision

When a version closes, flamin writes a **release proposal**: test results, open CRs, known risks, and a plain recommendation (release or hold) with reasons. The human decides through the release gate.

- Any version can be released.
- A version that is not released stays in the history. Work continues in the next version.
- `flamin release` closes the version and builds the proposal. The human's yes or no on the release gate is final for that version, either way. The next `cr-new` opens the following version (P-22).

### 16.4 Version record

`.flamin/versions/v<n>.md` holds: the CR list, approvals (with request ids), test results, the release proposal, and the release decision (released yes or no, date, approver).

---

## 17. Docs as memory

Agents forget between sessions. `.flamin/` remembers.

```
.flamin/business/{overview,glossary,actors,journeys,rules,states}.md
.flamin/technical/{overview,capabilities}.md
.flamin/analysis/<slug>/{requirements.md,plan.md}
.flamin/decisions/{stack.md,assumptions.md,baseline-amendments.md}
```

- The rules files (`CLAUDE.md`, and `AGENTS.md`, which Codex and Cursor both read) are rendered from one source, `kit/rules/core.md` (P-13), and stay short: a table of contents and the handful of rules that must never be broken. They point to `.flamin/`, they do not copy it.
- **Session-end reminder.** On `SessionEnd` or `Stop`, the engine checks the audit log for the session. If product code changed but nothing under `.flamin/` did, it shows a reminder, not a block. A block would only produce filler text to pass the gate. (Soft reminder, Heuristic trigger. On Codex the session-end hook runs only for the main thread and has 1 to 3 seconds, so the `Stop` hook carries the reminder there.)
- Quality of what is written stays **Soft**. A hook can see that a file changed, not that the content is true or useful.

---

## 18. Resume and idempotency

- Saying "continue" makes the Orchestrator run `flamin resume`. It resumes from the last completed step in `state.json` and reads the latest handoff. Finished work is never redone or duplicated. (Hard for engine steps, because each command knows it is done. Soft for work inside a step that was cut off mid-way; the handoff's State Ledger narrows the gap.)
- Every engine command is safe to run twice (Section 3.5).
- "Start over" is never the default answer to an interruption. Redoing finished steps duplicates specs and code.

---

## 19. Working lessons, as flamin practices

| Lesson | The flamin way |
|---|---|
| One session, one task | The Orchestrator gives each sub-agent one clear task with a "done" line. Unrelated requests start a new task. (Soft) |
| Batch interfaces, 5 or fewer | The Planner makes batches of 5 or fewer. Complex interfaces get their own batch. (Soft, `flamin plan` warns above 5) |
| Wrap up before context gets full | Handoff at 80% with the three required sections. (Section 6.6) |
| Give precise context | Agents cite exact file paths from `.flamin/` and the product. Handoffs list files to read. (Soft) |
| Specific requirements give better output | Intake and Step 1 ask for business context, feature points, boundary conditions and special rules. (Soft, with Hard mandatory items) |
| Images help | Mockups, diagrams and screenshots are welcome inputs at intake and Step 1, where the tool accepts images. (Soft) |
| Iterate the interface list before confirming | The Planner's plan stays editable until the human agrees; then Step 7 starts. (Hard gate on agreement) |
| Model first, then design, then code | The 13-step order. (Hard, Section 9) |
| Regenerate after changing a spec | `flamin lint --drift` flags stale output; `flamin generate` is the only way to refresh locked code. (Heuristic detect, Hard lock) |
| Read specs, not generated base code | Specs are the fast map of structure. (Soft) |
| Business rules in the aggregate root | `validateAggregate` extension point. (Hard wiring, Soft content) |
| Cross-module calls only through contracts | Section 13. |
| Mock a module that is not ready | Recorded mocks in Step 9, swapped when ready. (Soft, tracked by `flamin wire`) |
| Paste full error output | Agents paste full error text into their report; the Developer asks for it when missing. (Soft) |
| Say "continue", not "redo" | Section 18. |
| Team standards in one place | `.flamin/business/rules.md` and `.flamin/technical/overview.md`, rendered into each tool's rules file. (Soft) |
| Undo a wrong turn | Git is the undo button: each step ends at a commit, so a bad step is reverted with git. For larger changes, a new version with a CR. (Hard for committed work) |

---

## 20. Strength summary

| Mechanism | Claude Code | Codex | Cursor | Backstop |
|---|---|---|---|---|
| Phase and step order (engine commands) | Hard | Hard | Hard | Hard |
| Phase gate on agent launch | Hard | Hard (`tool_input.agent_type`, probed) | Hard | n/a |
| Lock levels on write | Hard (text check) | Hard (patch parse) | Hard for `Write` (probed, default model); Heuristic after for other tools | Hard |
| Module boundaries on write | Heuristic | Heuristic | Heuristic (before write for `Write`, after for other tools) | Heuristic |
| Per-agent path rules | Hard | Hard (hook `agent_type`, probed) | Soft + lease Heuristic | Heuristic |
| Delegation depth 1 | Hard | Hard | Heuristic | n/a |
| Up to 10 parallel | Hard | Hard | Soft + engine lease cap | n/a |
| Destructive command block | Heuristic detect, Hard block | Heuristic detect, Hard block | Heuristic detect, Hard block | n/a |
| Secret leak block | Heuristic detect, Hard block | Heuristic detect, Hard block | Heuristic detect, Hard block | Heuristic |
| Approval Gates | Hard (tool prompt in interactive modes; terminal otherwise, including `auto`, D-43) | Hard (terminal) | Hard (terminal, D-42) | n/a |
| Audit log with redaction | Hard write, Heuristic redaction | Hard write, Heuristic redaction; hosted tools not seen | Hard write, Heuristic redaction | n/a |
| State file safety | Hard | Hard | Hard | Heuristic (checksum) |
| Enforcement layer protection (P-19) | Hard for file tools, Heuristic for shell | Hard for file tools, Heuristic for shell | Hard for `Write`, Heuristic for shell | Heuristic (manifest hash) |
| Handoff structure | Hard (validation) | Hard | Hard | n/a |
| Handoff trigger at 80% | Soft + Heuristic | Soft | Soft + Heuristic | n/a |
| Clarification and assumptions | Soft questions, Hard "agreed" gate | same | same | n/a |
| Docs as memory | Soft | Soft | Soft | n/a |

---

## 21. Open questions

Each item has one recommendation: **Fix**, **Workaround** or **Drop**. P-01 to P-14 were approved as D-12 to D-25, and P-15 to P-18 as D-26 to D-29 (Appendix A). P-19 to P-26 were approved as D-30 to D-37 (Appendix A). Items marked "at build" are closed by probes during the initial kit implementation.

### 21.1 Common to all tools

| Id | Question | Recommendation |
|---|---|---|
| OQ-01 | Where do stack profiles live? | **Fix (P-01).** Masters in `kit/stacks/`, copied into `.flamin/stacks/` on approval. The product owns its copy. |
| OQ-02 | State format change on kit upgrade | **Fix (P-02).** Never automatic. Upgrade stops; `flamin upgrade --migrate` backs up `.flamin/`, runs a tested migration, behind an Approval Gate. |
| OQ-03 | Kit runtime floor | **Fix (P-03).** Python 3.11 or newer. 3.8 and 3.9 are end of life; 3.10 ends 2026-10-31. |
| OQ-04 | Python command per OS | **Fix (P-04).** `python3` on Linux and macOS, `python` on Windows. Files that name a Python command are machine-local and rendered per OS. |
| OQ-05 | Spec format | **Fix (P-05).** TOML, read with `tomllib`. |
| OQ-06 | 80% handoff trigger is not reliable | **Workaround (P-06).** Keep 80% as Soft; add a Hard step-boundary ledger so a lost context costs at most one step. Drop the "handoff every N steps" idea. |
| OQ-07 | Silent hook failure makes gates Soft without anyone noticing | **Fix (P-07).** Hook heartbeat checked by `flamin status` at the start of every session. |
| OQ-08 | Parallel cap | **Fix (P-08).** Claude Code: `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS = 10`. Codex: `max_threads = 10`. Cursor has no documented cap, so the engine lease cap of 10 is the control there. |
| OQ-09 | Per-tool custom commands | **Drop (P-09).** Plain language to the Orchestrator plus `flamin <verb>` in a terminal covers it. Codex deprecated its version and Cursor's is not in the checked official docs. |

### 21.2 Claude Code

| Id | Question | Recommendation |
|---|---|---|
| OQ-10 | Exact model strings in agent files | **Resolved.** The docs show `claude-opus-5-5` and `claude-sonnet-5` as valid full IDs. `claude-haiku-4-5-20251001` confirmed by a live launch on 2.1.228 (§5.4). |
| OQ-21 | Built-in agents and forks could stand in for flamin agents | **Fix.** Main-session `Agent(...)` allowlist plus `permissions.deny` for forks and built-ins (§4.1). |
| OQ-11 | Hook shell on Windows (Git Bash or PowerShell) | **Fix (P-04).** Exec form (`command` plus `args`), so no shell is involved. |

### 21.3 Cursor

| Id | Question | Recommendation |
|---|---|---|
| OQ-12 | Pre-write payload shape | **Resolved for the default model (§5.4).** `Write` carries `{file_path, content}` with the full new text; `Delete` carries `{file_path}`. Lock and boundary checks for `Write` move to `preToolUse` (Hard). The P-10 auto-revert stays for other edit tools. |
| OQ-22 | Edit tools on other Cursor models | **Workaround (P-15).** Only the default model was probed. `preToolUse` has no matcher and treats unknown tools with a `file_path` as writes; `afterFileEdit` auto-revert stays. Probe again when a plan with model choice is available. |
| OQ-13 | Model IDs | **Resolved (P-11).** `claude-opus-5-5`, `claude-sonnet-5` and `composer-2.5` confirmed on their Cursor model pages. |
| OQ-14 | Depth 1 (Cursor allows one extra level) | **Workaround.** `subagentStart` denies a launch whose parent is a known sub-agent, plus a Soft rule. Accept as Heuristic. |
| OQ-15 | Shell used for hook commands on Windows | **Resolved (§5.4).** PowerShell, from the workspace root. Machine-local `hooks.json` with `.\flamin.cmd` on Windows (P-17), `failClosed: true` so a break is loud. |
| OQ-16 | Custom modes and sandbox modes | **Drop.** Not needed (§5.3). |

### 21.4 Codex

| Id | Question | Recommendation |
|---|---|---|
| OQ-17 | Which spawn argument names the custom agent | **Resolved (§5.4).** `PreToolUse` on `collaborationspawn_agent` carries `tool_input.agent_type`; `SubagentStart` carries top-level `agent_type`. The `Agent` matcher never matches. Sub-agent tool calls also carry `agent_type`, so per-agent path rules become Hard on Codex (P-16). |
| OQ-18 | Sub-agent model override (issue report, not official docs) | **Workaround.** Keep the cheap check: the engine compares the hook's `model` field with the tier and warns on mismatch. Heuristic. |
| OQ-19 | Hooks skipped until trusted in `/hooks` | **Workaround.** README first-run step plus `flamin doctor` warning plus the heartbeat check (P-07). Probed (§5.4): trust is a hash per hook, so any change to `.codex/hooks.json` makes the changed hooks untrusted and they are skipped silently. `flamin init` and `flamin upgrade` tell the human to re-trust after every change (P-18). |
| OQ-20 | `max_threads` default is 6 | **Fix.** Set `max_threads = 10` in the shared `.codex/config.toml` (already in §4.2). |

---

## Appendix A. Decisions log

| Id | Decision |
|---|---|
| D-01 | Build folder is the kit folder `flamin_v3_Claude`. Everything outside it is reference only and must never be modified. |
| D-02 | `flamin_v3_Claude` is a clean master kit. It never hosts a real product. To start a product, the folder is copied and renamed (for example `flamin_cinema`). |
| D-03 | Eight agents: the Orchestrator, then Business → Analyst → Architect → Planner → Designer → Developer → Tester. No Guardian agent. |
| D-04 | The kit runs on Linux, macOS and Windows. Products may target web, desktop, backend, iOS and Android. |
| D-05 | Two runtimes: a small fixed kit runtime for the engine, and a product runtime recommended per product after intake. |
| D-06 | Clarification happens as early as possible, before any spec is locked. |
| D-07 | Versions: v1 is the first build from scratch, then v2, v3, and so on. No D cycles, no major or minor. Any version can become a production release when the human agrees to a release proposal from flamin. |
| D-08 | Models are chosen by tier (Deep, Balanced, Fast), mapped per tool. |
| D-09 | Audit logs: JSON Lines inside the project, 1 GB or 1 year, whichever comes first. |
| D-10 | Handoff at 80% context. Delegation depth 1. Up to 10 agents in parallel. One writer at a time on state files. No hard cap on tools. |
| D-11 | PreInvocation safety hook and configurable policy files are deferred. Described as a future enterprise option. Not built. |

Approved in review round 2 (2026-09-24). References such as "(P-04)" in the body of this document point to the matching decision below.

| Id | Was | Decision |
|---|---|---|
| D-12 | P-01 | Stack profile masters in `kit/stacks/`, copied into `.flamin/stacks/` on approval. |
| D-13 | P-02 | State migrations on kit upgrade are never automatic: backup, tested migration, Approval Gate. |
| D-14 | P-03 | Kit runtime floor is Python 3.11. |
| D-15 | P-04 | `python3` on Linux and macOS, `python` on Windows. Files naming a Python command are machine-local and rendered per OS; Codex uses `command_windows` in one shared file. |
| D-16 | P-05 | Design specs are TOML. |
| D-17 | P-06 | 80% handoff stays Soft; a Hard step-boundary ledger limits loss to one step. |
| D-18 | P-07 | Hook heartbeat checked by `flamin status` at the start of every session. |
| D-19 | P-08 | Parallel limit of 10: tool setting on Claude Code and Codex, engine lease cap everywhere. |
| D-20 | P-09 | No per-tool custom commands. Plain language plus `flamin <verb>`. |
| D-21 | P-10 | Cursor: auto-revert lock and boundary breaches through `afterFileEdit` until a pre-write check is proven. |
| D-22 | P-11 | Cursor tiers: Claude Opus 5.5 (`claude-opus-5-5`), Claude Sonnet 5 (`claude-sonnet-5`), Composer 2.5 (`composer-2.5`). |
| D-23 | P-12 | Every Unknown gets a logging-only probe at build, with results in VERIFICATION.md. |
| D-24 | P-13 | One tool-neutral rules source (`kit/rules/core.md`) rendered into `CLAUDE.md` and `AGENTS.md`; Cursor reads `AGENTS.md`, so no `.cursor/rules/` file. |
| D-25 | P-14 | The forbidden-name check from the build task matches only at the start of a word (case-insensitive), so a match in the middle of a code identifier, such as a DTO converter class name in a read-only guide, is not counted. The exact pattern lives in the build task, not in this file, so this file never contains the name. |

Approved in review round 3 (2026-09-24), from the live probes in §5.4.

| Id | Was | Decision |
|---|---|---|
| D-26 | P-15 | Cursor `preToolUse` runs with no matcher. The engine treats any unrecognised tool that carries a `file_path` as a write, logs it, and keeps the `afterFileEdit` auto-revert as the backstop, because only the default model's payload is proven. |
| D-27 | P-16 | Per-agent path rules are Hard on Codex, using the top-level `agent_type` that sub-agent tool calls carry; calls without it are the main-thread Orchestrator. Falls back to Soft plus lease if a Codex update drops the field. |
| D-28 | P-17 | Windows hook commands for Cursor and Codex call `.\flamin.cmd`, because both tools run hooks through PowerShell, which does not run a file from the current folder without `.\`. |
| D-29 | P-18 | After any change to `.codex/hooks.json` (`init`, re-render or `upgrade`), flamin tells the human to re-trust the hooks in `/hooks`, because Codex silently skips changed hooks until then. |

Approved in review round 4 (2026-09-24).

| Id | Was | Decision |
|---|---|---|
| D-30 | P-19 | Agents may not write to the enforcement layer: `kit/`, launchers, `.claude/`, `.codex/`, `.cursor/`, `CLAUDE.md`, `AGENTS.md`, `.git/`, `.flamin/stacks/`. `kit/MANIFEST` hashes are checked by `check-staged` and `doctor`. Product stack profile changes go through the new-profile gate. Reason: an agent that can edit the engine, a launcher or a hook config can switch every other check off. |
| D-31 | P-20 | All gate decisions (requests, answers, approved hashes, assumption status) live only in the engine-owned `.flamin/approvals.json`. Reason: agents may write `assumptions.md` and `.flamin/decisions/`, so a status kept there could be self-approved. |
| D-32 | P-21 | The tool `ask` path is used only in an interactive permission mode. Any other or unknown mode: deny, then terminal path. Each mode is probed at build. |
| D-33 | P-22 | A CR joins the open version until its CR list is approved, then the next one. `flamin release` closes the version. The build number goes up by 1 per release candidate, and at no other time. |
| D-34 | P-23 | `cr-approve` records affected modules and the earliest step per CR; the engine resets those modules' step state from that step. |
| D-35 | P-24 | CI runs `check-staged --range <base>..<head>`. |
| D-36 | P-25 | On Codex and Cursor the Orchestrator is the main thread, with instructions in `AGENTS.md`, unless the build proves the tool can set a custom main agent. Only seven worker agent files are rendered. Launching an agent named `orchestrator` is denied. |
| D-37 | P-26 | An audit line that cannot get the lock goes to a per-session spill file, merged at the next append. Audit lines are never dropped. |

Approved in review round 5 (2026-09-26), from the initial kit implementation findings (VERIFICATION.md §3 and §4).

| Id | Was | Decision |
|---|---|---|
| D-38 | K-1 | Codex uses `[agents] max_concurrent_threads_per_session = 10` (`max_threads` is a legacy alias). `max_depth = 1` stays as extra protection while `flamin doctor` confirms Codex accepts it (`codex exec --strict-config`); it is not in the Codex docs. The Hard depth control on Codex is the engine launch gate. |
| D-39 | K-2 | Codex hooks live only inline in `.codex/config.toml`, with TOML `command_windows`: one representation per layer, as the Codex docs advise. No `.codex/hooks.json` is rendered. The D-29 re-trust notice applies to any change of the hook tables in `.codex/config.toml`. |
| D-40 | K-3 | A deny travels as JSON in each tool's own reply schema; the exit code is secondary. Codex and Cursor: JSON deny, exit 0. Claude Code: JSON deny plus exit 2, reason also on stderr (exec form, no shell). The Codex Windows command resolves the project root from git and ends with `; exit $LASTEXITCODE`; this replaces D-28's `.\flamin.cmd` for Codex only. Cursor keeps `.\flamin.cmd`. `flamin doctor` proves the rendered Codex hook runs on the installed Codex version. |
| D-41 | K-4 | Agents that must write get Codex `sandbox_mode = "workspace-write"` and no Cursor `readonly` flag. Their writes are limited by per-agent path rules (Hard on Claude Code and Codex, Soft plus lease on Cursor). The hook denies every shell call from an agent whose neutral tool list has no `shell` (Business, Analyst, Architect, Planner): Hard on Claude Code and Codex, Soft on Cursor. A read-only sandbox is used only for an agent with no write paths. |
| D-42 | K-5 | Cursor gates always deny and send the human to `flamin approve <id>` in a terminal, because Cursor hook input has no permission-mode field. Strength stays Hard. |
| D-43 | K-6 | Claude Code `auto` stays on the deny-then-terminal path. Relaxing it needs a new decision backed by a live interactive probe on each surface (terminal, VS Code extension, desktop). |
| D-44 | K-7 | Cursor also loads `.claude/agents/`, `.codex/agents/` and, with Third-Party Imports on (the default), Claude Code hook files. The engine answers any Cursor payload in Cursor's format, denies launches of `orchestrator`, and makes gate requests and audit lines idempotent per tool call: key = `tool_use_id`, else `generation_id` + hook event + hash of the tool input. Probe again when the Cursor CLI is available. |
| D-45 | K-8 | A root `.gitattributes` is part of the kit and of the enforcement layer: LF for `flamin`, `kit/githooks/*` and `.flamin/**/*.json`; CRLF for `*.cmd`. `flamin` and the hook templates are committed as mode 100755. `flamin init` writes `.git/hooks/pre-commit` with LF and the executable bit. State checksums ignore CRLF versus LF only. |
| D-46 | K-9 | Master-kit maintenance mode turns on only by a human-only terminal command that writes a git-ignored flag. With the flag and no `.flamin/`, the product flow does not apply and D-30 is lifted for kit files only; kit changes must pass the engine tests and refresh `kit/MANIFEST`. `flamin init` refuses while the flag is set. `flamin doctor --kit` fails if the flag exists. "No product found" alone never enables it. The master kit keeps shipping the rendered adapters. How maintenance sessions get write tools is not decided here. |

Approved on 2026-09-28 (kit maintenance request, human-issued).

| Id | Was | Decision |
|---|---|---|
| D-47 | kit maintenance | A product uses only the AI tool(s) the human chose, recorded by the engine in `state.json` as `tools` (a subset of claude, codex, cursor). `flamin init` takes `--tool` if given, else the detected tool, else Claude Code; `all` is an explicit opt-in. Only Claude Code is auto-detected (`CLAUDECODE=1` or `CLAUDE_PROJECT_DIR`), because no Codex or Cursor marker for the agent's own shell is documented. `--tool` on an existing product adds a tool and never silently removes one. `flamin doctor`, `flamin status` (hook warning), the stale-adapter check and `flamin upgrade` act only on the product's tools: Claude Code checks run only for Claude Code, Codex checks only for Codex (or `--probe-codex`), and the Codex re-trust note appears only when Codex is used. Reason: checks and notes for a tool the product does not use are noise that hides real warnings, and running Codex on a Claude Code machine is wasted and confusing. A product without `tools` (made before D-47) keeps the old behaviour by inferring its tools from the adapter files present, so no state migration is needed. `flamin doctor --fix --tool <x> --prune` deletes the adapter files of other tools through the Approval Gate for deletes (§15.3). |
| D-48 | kit maintenance, 2026-10-02 | Supersedes the multi-tool and prune parts of D-47. Exactly one tool is active per product. Fresh `flamin init` archives every inactive adapter and root prompt under `.flamin/inactive-adapters/`; that path is outside Claude Code, Codex and Cursor discovery. Codex and Cursor use the same main-chat Orchestrator prompt from `kit/agents/orchestrator.md` through `AGENTS.md`; Claude Code selects its Orchestrator agent as the main session. Codex/Cursor `orchestrator` sub-agent files are archived if encountered. `flamin init --tool <different>` opens a human `tool-switch` Approval Gate; a pending or rejected request leaves files and state untouched. Approved switches restore a preserved snapshot, re-render the selected adapter, reset its hook heartbeat, and record one active tool. A backup snapshot remains even when rendering replaces a customized managed file. `doctor --fix` repairs only the current tool and archives stray inactive files; `doctor --tool` cannot switch. Hooks from inactive tools deny pre-tool calls. Legacy products with ambiguous tool selection must explicitly choose a tool through the same gate before init or upgrade proceeds. `--tool all` and destructive `--prune` are retired. |

### Proposed decisions (awaiting approval)

| Id | Was | Proposal | Status |
|---|---|---|---|
| P-27 | K-6 (optional sub-item) | Shared `.claude/settings.json` sets `permissions.defaultMode: "default"`, so terminal sessions start in Manual and keep the in-tool prompt path. Reason: from Claude Code v2.1.283, `auto` is the built-in starting mode for interactive terminal and VS Code sessions, so under D-43 most gates would go to the terminal. The VS Code extension ignores project settings for its starting mode. | Not answered in round 5 |

## Appendix B. Doc links used for the capability matrix

Checked on 2026-09-24. "Fetched" means the full page was read, not a search excerpt.

| Ref | Link | Status |
|---|---|---|
| C1 | https://code.claude.com/docs/en/hooks | Fetched |
| C2 | https://code.claude.com/docs/en/sub-agents | Fetched |
| C3 | https://code.claude.com/docs/en/agent-sdk/subagents | No longer cited (C2 covers it) |
| X1 | https://developers.openai.com/codex/hooks | Fetched |
| X2 | https://developers.openai.com/codex/subagents | Fetched |
| X3 | https://developers.openai.com/codex/custom-prompts | Fetched |
| X4 | https://developers.openai.com/codex/models | Fetched |
| X5 | https://developers.openai.com/codex/guides/agents-md.md | Fetched |
| U1 | https://cursor.com/docs/hooks | Fetched |
| U2 | https://cursor.com/docs/subagents | Fetched |
| U3 | https://cursor.com/docs/rules | Fetched |
| U4 | https://cursor.com/docs/customize-cursor | Not checked; no longer needed (P-09) |
| U5 | https://cursor.com/docs/models-and-pricing | Fetched |
| U6 | https://cursor.com/docs/models/claude-opus-5-5 | Fetched (Model ID `claude-opus-5-5`) |
| U7 | https://cursor.com/docs/models/claude-sonnet-5 | Fetched (Model ID `claude-sonnet-5`) |
| U8 | https://cursor.com/docs/models/cursor-composer-2-5 | Fetched (Model ID `composer-2.5`) |

Python sources used for P-03 and P-04:

| Ref | Link | Status |
|---|---|---|
| P1 | https://devguide.python.org/versions/ | Fetched |
| P2 | https://docs.python.org/3/using/windows.html | Fetched |
| P3 | https://peps.python.org/pep-0394/ | Fetched |
| P4 | https://docs.python.org/3/using/mac.html | Fetched |
| P5 | https://packages.ubuntu.com/jammy/python3 | Fetched (full page, 2026-09-24): default `python3` is 3.10.6 on amd64 and i386, 3.10.4 on arm64 and other ports |
