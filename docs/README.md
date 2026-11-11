# flamin

flamin is a kit for building software with a team of AI agents. You talk to it in plain language. Eight agents do the work in a fixed order, and a small engine keeps the line honest: it knows which step the product is on, which files are locked, and which actions need your "yes".

- **Design:** [DESIGN.md](DESIGN.md) (approved, decisions D-01 to D-46). It says, for every rule, whether it is Hard, Soft or Heuristic.
- **Day-to-day flow:** [WORKFLOW.md](WORKFLOW.md).
- **What was tested and how:** [VERIFICATION.md](VERIFICATION.md).
- **Build handoffs:** [BUILD_LOG.md](BUILD_LOG.md).
- **Versioned master-kit archives and GitHub package:** [RELEASING.md](RELEASING.md).

The three original reference guides (`Collaborative Workflow.md`, `Generated Code Architecture Summary.md`, `Tips and Tricks.md`) are kept locally in `docs/archive/local-reference/`. That folder is ignored by Git and excluded from release archives. The guides describe another platform; their features are not flamin features (DESIGN §0, §19).

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

`flamin doctor` lists anything that blocks the kit on this machine: the Python command, git, the hooks path, stale locks, adapter files, untrusted hooks, kit integrity, absolute paths and executable bits. Where Codex is installed it also checks that Codex accepts the `[agents]` keys and that the rendered Codex hook denies a destructive command in the shell Codex uses. `flamin doctor --probe-codex` goes one step further and proves the hooks run inside the installed Codex (one model call; the project and its hooks must be trusted).

## Start a product

This folder is the **master kit**. It never hosts a real product (`flamin doctor --kit` checks that it stays clean).

1. Copy the whole folder and rename the copy, for example `flamin_cinema`.
2. Open the copy in Claude Code, Codex or Cursor.
3. Say **"start a new project"**. The Orchestrator runs `flamin status`, then `flamin init`, then the Business agent starts intake: product name, purpose, and who will use it.

`flamin init` checks the Python runtime, creates a git repo if needed, creates `.flamin/` at phase 0 and version v1, writes the pre-commit backstop, renders the adapters for the AI tool you are using (see below) for this OS and writes `.gitignore` entries. It never overwrites an existing product: one folder, one product.

### Which AI tool a product uses (D-48)

A product has one active AI tool. Its Orchestrator runs in the main chat; the seven other agents are available as workers.

- `flamin init` inside Claude Code detects it (the `CLAUDECODE` marker) and prints `Adapters for claude (detected: Claude Code)`. With no marker it defaults to Claude Code. Codex and Cursor are not auto-detected: their docs name no marker for the agent's own shell, so name them with `--tool`.
- `flamin init --tool codex` (or `cursor`) selects that tool. On an existing product, changing the tool opens a request; the human answers `flamin approve R-000n --yes` in a terminal, then repeats the init command. A rejected request leaves the current tool active.
- The choice is stored in `.flamin/state.json` (`tools` contains one name). Older products with several discoverable adapters must choose one through the same approval gate.
- A copy of the master kit carries all three adapters. Init moves inactive directories and prompts into `.flamin/inactive-adapters/`, where the tools cannot discover them. It keeps a snapshot when switching back, including customized files. `flamin doctor` reports any stray inactive adapter and `flamin doctor --fix` archives it. `--tool all` and `--prune` are retired.
- `flamin doctor` runs the Claude Code checks (settings files current; the PreToolUse hook run exactly as Claude Code runs it must deny a destructive command; last hook call) only when Claude Code is a product tool, the Codex checks only when Codex is, and the Cursor checks (agent and hook files current; the `preToolUse` command run the way Cursor runs it must deny a destructive command; last hook call) only when Cursor is. For a tool the product does not use it prints, for example, `Codex: not used by this product; checks skipped.` The Cursor check proves the hook command at shell level; it cannot prove that Cursor itself runs it.

### Per tool, once per machine

- **Claude Code:** trust the workspace when asked. The main session runs as the flamin Orchestrator (`.claude/settings.json` sets `"agent": "orchestrator"`). Hooks live in the machine-local `.claude/settings.local.json`.
- **Codex:** trust the project, then open `/hooks` and trust the flamin hooks. Codex skips a changed hook silently until it is trusted again, so do this after every `flamin init`, re-render or `flamin upgrade` that changes the hook tables in `.codex/config.toml` (the only place Codex hooks live, D-39). Then run `flamin doctor --probe-codex`. On Windows, if tool calls fail inside the `codex.exe` bundled in `~/.codex/.sandbox-bin`, use the copy in `~/.codex/plugins/.plugin-appserver` (a machine setup issue, not a kit rule).
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
| `kit-maintenance on/off/test/status` | Master-kit maintenance mode (on and off are human-only) |

## Stack profiles

Shipped in `kit/stacks/` and copied into the product on approval: `java-spring`, `python-fastapi`, `typescript-node` (Express), `javascript-node` (Express), `typescript-react` (web), `react-native` (iOS and Android), `minimal` (no layers). DESIGN §11.6 explains how to add a profile. iOS device builds need macOS with Xcode or a cloud build service; on other machines flamin still generates, lints and unit-tests the code.

## Folder layout

```
flamin, flamin.cmd   one-line launchers
kit/                 VERSION, MANIFEST, rules/, engine/, agents/, adapters/, stacks/, githooks/, ci/
docs/                this README, DESIGN, WORKFLOW, VERIFICATION, BUILD_LOG; local-only historical guides in archive/local-reference/
.flamin/             product state (product copies only; created by flamin init)
```

The engine's unit tests live in `kit/engine/tests/`:

```
python -B -m unittest discover -s kit/engine/tests -t kit/engine/tests
```

## Continuous integration

`kit/ci/flamin-check.yml` is an optional GitHub Actions workflow for a shared product repository. Once the FLAMIN v4 verifier is on the base branch, copy the file to `.github/workflows/flamin-check.yml`. The job checks the proposed merge with the base branch's snapshot verifier.

## Maintaining the master kit (D-46)

The master kit ships the rendered adapters, so opening it in Claude Code makes the session the flamin Orchestrator, which cannot edit files. To change the kit:

```
flamin kit-maintenance on       # a human, in a terminal; refused where a product lives
flamin kit-maintenance test     # after the change: runs the engine tests, refreshes kit/MANIFEST, records the pass
flamin kit-maintenance off      # a human, in a terminal
```

While the mode is on, the main assistant handles kit files (`kit/`, the launchers) directly; the seven product agents do not edit kit infrastructure. Generated tool config and rules files are updated through the engine renderer; `.git/` remains protected. External changes use M-prefixed requests stored in protected, git-ignored `.flamin-maintenance/`, without creating a product. A human lists requests with `flamin approve` and answers the exact request in a terminal with `flamin approve M-0001 --yes` (or `--no`); retry the same operation afterward. Approval is single-use and bound to the workspace and exact action. Recognized literal GitHub reads are allowed; ambiguous requests remain gated. Maintenance does not grant blanket approval for external changes. The pre-commit check still refuses kit changes that did not pass `flamin kit-maintenance test`; that command currently runs the full suite and has no scope option. `flamin init` is refused and `flamin doctor --kit` fails until the mode is off, so a shipped kit never carries the flag. Engine hook source changes apply on the next hook invocation; adapter configuration changes may require reloading the tool and renewing hook trust.

## Codex shell environment

The Codex adapter sets `shell_environment_policy.inherit = "core"`, limiting
which parent environment variables reach shell commands. Projects that depend
on custom environment variables must configure those explicitly. The two
`CLAUDE_CODE_MAX_*` values are passed to Claude subprocesses; they do not set
Codex agent limits. Codex uses its native `[agents]` settings for depth 1 and
up to 10 concurrent agents. The main chat remains the Orchestrator, with seven
rendered worker agents.

Adapter changes belong in `kit/adapters/codex/config.toml`. After maintenance
tests pass, `flamin doctor --fix --tool codex` regenerates the master adapters;
do not edit `.codex/config.toml` directly.

## Future option (not built)

A PreInvocation safety filter for prompts, with configurable policy files, is a future enterprise option (DESIGN §15.4). Today's policy patterns are built-in defaults.

## Human-controlled maintenance commits

Stage the intended files first. `flamin kit-maintenance test --scope test_commit_validation test_maintenance_approvals` runs only named unittest scopes against a temporary copy of the index. It records the exact staged fingerprint; unstaged contents are excluded. Failed validation clears earlier scoped evidence and authorization. The full suite is not run.

Human terminal commands after staging:

```powershell
.\flamin.cmd kit-maintenance test --scope test_commit_validation test_maintenance_approvals
.\flamin.cmd kit-maintenance authorize-commit --validation scoped --reason "Focused checks are sufficient" --yes
.\flamin.cmd check-staged
git commit -m "Support human-controlled maintenance validation"
```

To explicitly skip tests, omit the test command and use `flamin kit-maintenance authorize-commit --validation skipped --reason "Human accepts committing without tests" --yes` instead. Agents cannot authorize either choice.

Authorization refreshes and stages only `kit/MANIFEST` from indexed blobs. It refuses to overwrite unstaged manifest edits and preserves other staged/unstaged files. It binds workspace, HEAD, paths, contents and modes; index/HEAD changes invalidate it. Protected `.flamin-maintenance/approvals.json` records the choice, reason, timestamp and human full-suite waiver. Scoped tests are never reported as a full-suite pass; skipped tests are never reported as a pass. Manifest, secret, lock and other applicable commit checks remain active. Release/publication approvals remain separate. Snapshots currently require resolved regular files; symlinks, submodules and conflicted indexes are rejected.

The existing full-suite command remains available without --scope, only when the human chooses it. Hook policy distinguishes literal document text from executed human-only commands for supported Python heredoc document writes; unknown forms remain conservative.

## Answers, approval and worker tool routing

The human answers questions in chat. Business records exact answers in its owned Markdown using native editing and returns intake values to Orchestrator, which runs intake engine commands. Formal assumption approval is a separate human-only terminal action.

Before delegation, inspect role tool/path permissions. Native read/search tools differ by host; Orchestrator supplies current file contents when the worker has no permitted read route. Codex workers with writing permission use apply_patch within their ownership. A role without shell permission cannot use exec_command, PowerShell, Bash or shell reads. D-41 denies those calls while preserving authorized native document editing. Orchestrator supplies context and engine execution, but cannot take over the owner's document edit. This differs from rerouting a denied write or switching identity to evade a hook.

Product adapters receive the repair through human-run flamin upgrade, not direct edits to generated files. After staging this repair in the master kit, validate only its named scopes and let the human authorize the commit:

```powershell
.\flamin.cmd kit-maintenance test --scope test_assumption_tool_routing test_round5.TestShellByAgentsWithoutShell test_round5.TestRenderRound5 test_commit_validation.TestCommitValidation.test_document_text_is_not_human_authorization_execution
.\flamin.cmd kit-maintenance authorize-commit --validation scoped --reason "Assumption tool-routing checks passed" --yes
.\flamin.cmd check-staged
```

Once the master repair is ready and its manifest is current, a human upgrades the existing product from its terminal:

```powershell
Set-Location C:\Users\WJKWEEJI\Code\Playground\TestPete
.\flamin.cmd upgrade --from C:\Users\WJKWEEJI\Code\TocoAI\flamin_v3_Claude --yes
```

Then restart/reload agent instructions as needed and verify the next real chat answer is recorded by Business using native editing, with Orchestrator supplying context if reads are unavailable. Do not fabricate answers or approve assumptions to test the flow. Keep product questioning paused until the repair is installed. Literal human-only command references in supported Python document writes are data; any genuinely executed human-only command in the trailing shell statements remains gated. Unknown or dynamic script forms remain conservative.
