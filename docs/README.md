# flamin

flamin is a kit for building software with a team of AI agents. You talk to it in plain language. Eight agents do the work in a fixed order, and a small engine keeps the line honest: it knows which step the product is on, which files are locked, and which actions need your "yes".

- **Design:** [DESIGN.md](DESIGN.md) (approved, decisions D-01 to D-37). It says, for every rule, whether it is Hard, Soft or Heuristic.
- **Day-to-day flow:** [WORKFLOW.md](WORKFLOW.md).
- **What was tested and how:** [VERIFICATION.md](VERIFICATION.md).
- **Build handoffs:** [BUILD_LOG.md](BUILD_LOG.md).

The three other guides in this folder (`Collaborative Workflow.md`, `Generated Code Architecture Summary.md`, `Tips and Tricks.md`) are read-only background about another platform. Their features are not flamin features (DESIGN §0, §19).

## First-run check (once per machine)

The kit runtime is Python 3.11 or newer, standard library only. No other install is needed for the engine.

| OS | Check | flamin uses |
|---|---|---|
| Linux, macOS | `python3 --version` | `python3` |
| Windows | `python --version` | `python` |

On Windows, if `python` opens the Microsoft Store, install Python from python.org or the Python install manager and turn off the "App execution alias" for Python. Then run:

```
flamin doctor          # Linux, macOS: ./flamin doctor    Windows PowerShell: .\flamin.cmd doctor
```

`flamin doctor` lists anything that blocks the kit on this machine: the Python command, git, the hooks path, stale locks, adapter files, untrusted hooks, kit integrity and absolute paths.

## Start a product

This folder is the **master kit**. It never hosts a real product (`flamin doctor --kit` checks that it stays clean).

1. Copy the whole folder and rename the copy, for example `flamin_cinema`.
2. Open the copy in Claude Code, Codex or Cursor.
3. Say **"start a new project"**. The Orchestrator runs `flamin status`, then `flamin init`, then the Business agent starts intake: product name, purpose, and who will use it.

`flamin init` checks the Python runtime, creates a git repo if needed, creates `.flamin/` at phase 0 and version v1, writes the pre-commit backstop, renders the tool adapters for this OS and writes `.gitignore` entries. It never overwrites an existing product: one folder, one product.

### Per tool, once per machine

- **Claude Code:** trust the workspace when asked. The main session runs as the flamin Orchestrator (`.claude/settings.json` sets `"agent": "orchestrator"`). Hooks live in the machine-local `.claude/settings.local.json`.
- **Codex:** trust the project, then open `/hooks` and trust the flamin hooks. Codex skips a changed hook silently until it is trusted again, so do this after every `flamin init`, re-render or `flamin upgrade` that changes the hooks in `.codex/config.toml`. On Windows, if tool calls fail inside the `codex.exe` bundled in `~/.codex/.sandbox-bin`, use the copy in `~/.codex/plugins/.plugin-appserver` (a machine setup issue, not a kit rule).
- **Cursor:** trust the workspace. Hooks live in the machine-local `.cursor/hooks.json` (with `failClosed: true`). Cursor reads the root `AGENTS.md`.

A teammate who clones a product has no hooks until they run `flamin init` on their machine. The first `flamin status` of a session says so loudly.

## How you drive it

Speak plain language to the Orchestrator: "continue", "add a cancel booking feature", "release v2". Before acting it shows three lines:

```
1. Understood: <your intent in plain words>
2. Planned:    <agent> plus `flamin <verb>`
3. Because:    <the rule or document that supports it>
```

Every plain-language request maps to a `flamin <verb>` command, which you can also run yourself in a terminal. Some decisions are yours only. The engine accepts them from you in a terminal:

```
flamin approve R-0012 --yes      # or --no; no answer means no
flamin approve A-003 --yes       # agree to an assumption
```

## Commands

| Command | Purpose |
|---|---|
| `init`, `status`, `doctor`, `resume` | Set up, see where you are, check the machine, carry on after a break |
| `intake` | Step 0 answers (`--set key=value`), `--check` for missing items, `--stack <profile>` for the stack gate |
| `analyze`, `model`, `baseline-review`, `baseline-amend` | Phase 1: requirements, modules, data model, aggregates, baseline |
| `plan`, `design`, `generate`, `lint`, `develop`, `wire`, `module-done` | Phase 2 per module (Steps 6 to 10) |
| `integration-test`, `architecture-review`, `release` | Phase 3 (Steps 11 to 13) |
| `cr-new`, `cr-approve`, `approve` | Change requests and every gate answer |
| `audit` | The audit log as readable lines; `--stats` shows steps per agent per task |
| `upgrade --from <newer kit>` | Replace kit files only; never touches `.flamin/` |
| `check-lock`, `check-boundary`, `check-phase`, `check-staged` | The checks, also used by the pre-commit hook and CI |
| `hook`, `handoff`, `rebuild-locks` | Tool hook entry point, handoff write and validation, lock cache rebuild |

## Stack profiles

Shipped in `kit/stacks/` and copied into the product on approval: `java-spring`, `python-fastapi`, `typescript-node` (Express), `javascript-node` (Express), `typescript-react` (web), `react-native` (iOS and Android), `minimal` (no layers). DESIGN §11.6 explains how to add a profile. iOS device builds need macOS with Xcode or a cloud build service; on other machines flamin still generates, lints and unit-tests the code.

## Folder layout

```
flamin, flamin.cmd   one-line launchers
kit/                 VERSION, MANIFEST, rules/, engine/, agents/, adapters/, stacks/, githooks/, ci/
docs/                this README, DESIGN, WORKFLOW, VERIFICATION, BUILD_LOG and the three guides
.flamin/             product state (product copies only; created by flamin init)
```

The engine's unit tests live in `kit/engine/tests/`:

```
python -B -m unittest discover -s kit/engine/tests -t kit/engine/tests
```

## Continuous integration

`kit/ci/flamin-check.yml` is a sample GitHub Actions workflow. Copy it to `.github/workflows/` in a product. It runs the engine tests and `flamin check-staged --range <base>..<head>` on Linux, macOS and Windows runners.

## Future option (not built)

A PreInvocation safety filter for prompts, with configurable policy files, is a future enterprise option (DESIGN §15.4). Today's policy patterns are built-in defaults.
