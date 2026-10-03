# flamin verification

What was tested, how, and the result. Every result below comes from a real run on 2026-09-24/25. Where something was not tested, this file says so and says why. "Verified" means run and observed; "inferred" means reasoned from docs or code, not observed; "unknown" means neither.

## Test machine

| Item | Version |
|---|---|
| OS | Windows 11 Enterprise 10.0.26100 |
| Kit runtime | Python 3.13.3 (`python`; `python3` is the Windows Store shortcut on this machine) |
| Claude Code | 2.1.281 |
| Codex | codex-cli 0.153.4 (the copy in `~/.codex/plugins/.plugin-appserver`) |
| Cursor | 3.21.18 (desktop app only; the `agent` CLI is not installed) |
| Product runtimes for the examples | Node 22.22.0, TypeScript 7.0.2, pytest 9.1.1 + FastAPI 0.141.1 + httpx 0.28.1 (in a separate virtual environment outside the kit), Java 21 + Maven 3.9.10 + Spring Boot 3.2.0 |

All test products lived in temporary folders outside the kit and were deleted afterwards.

## 1. Engine unit tests

`python -B -m unittest discover -s kit/engine/tests -t kit/engine/tests` → **Ran 121 tests, OK** (111 from the first build, 10 added for review round 5 in `test_round5.py`).

Every check has unit tests: state lock, atomic writes, checksums (including CRLF checkouts), audit redaction, spill and retention, secret patterns, lock levels (Python and TypeScript markers, CRLF, regeneration bypass, broken markers), module and layer boundaries (Python, TypeScript relative imports, Java packages, no-layers profile), phase and step order, the launch gate, destructive, human-only and gated shell patterns, Codex patch parsing, the generator (determinism, body carry-over, orphans, Step 7 completion), lint (roles, transient fields, extension point names, secret outputs, drift), every shipped profile generating, the pre-commit backstop through a real `git commit`, the kit manifest, `doctor --kit`, adapter rendering, upgrade and the migration gate, and every tool's hook reply format.

## 2. Acceptance tests

| Test | How | Result |
|---|---|---|
| **Portability** | Copied the master kit to a temp folder, renamed it `flamin_demo`, removed nothing, edited nothing. Ran Claude Code headless: `claude -p "start a new project"`. | **Pass.** The main session ran as the Orchestrator, ran `.\flamin.cmd status`, then `flamin init`. `.flamin/`, `.claude/settings.local.json` and the pre-commit hook were created. It stopped at the 8 mandatory intake items and asked for them. A second session's `flamin status` ran through the hook with no health warning. |
| **Cross-OS** | Sample CI job `kit/ci/flamin-check.yml` with a Linux, macOS and Windows matrix. | **Not tested** on Linux or macOS: this repository has no remote and no CI runner access from this machine. Windows verified locally. |
| **Intake halt** | `flamin intake --set name=cinema --set purpose=sell tickets --check` | **Pass.** Printed `HALT: mandatory intake items still missing` with the 6 missing items; exit 2. `flamin analyze` refused: `Step 1 refused: intake is not complete (HALT)`. |
| **Assumption blocked until "agreed"** | An agent wrote `## A-001 ... - Status: agreed` into `assumptions.md`, then `flamin analyze booking`. | **Pass.** `Step 1 refused: assumption(s) still open: A-001`. After `flamin approve A-001 --yes` (terminal) the step ran. Deleting the block from the notes does not close it (unit test). |
| **Examples per profile** | "member register + query by phone" through the full engine flow (intake, stack gate, Steps 1-10), extension logic in `validateAggregate`, tests written in the profile's test path, then `flamin module-done` (lock and boundary scan). | **Pass for all seven**, see the table below. |
| **Mobile** | `react-native`: generate, lint, unit tests of the feature, adapter and api-client layers with `node --test`, and `tsc --noEmit` over logic and screens (after `npm install` of `react`, `react-native`, types). | **Pass** (4/4 tests, tsc clean). **Not run:** device builds, iOS builds (this is Windows; iOS needs macOS with Xcode or a cloud build service), Android device or emulator builds, store submission. |
| **Gates: tool hook and pre-commit** | For each case: a Claude Code `PreToolUse` payload to `flamin hook`, then the same change made by hand and committed with `git commit` (real pre-commit hook). | **Pass, all four, both layers.** FULLY_LOCKED edit: hook deny, commit refused. Edit outside an extension point: hook deny (`line 9 is outside every extension body`), commit refused. Direct import of another module: hook deny, commit refused (`Cross-module calls go only through contracts`). Phase 2 step before the baseline: `flamin plan` refused, hook deny, commit refused. An edit inside the extension body passed both. |
| **Hooks: destructive, fake key, release, audit** | `rm -rf / --no-preserve-root`; a Write with an `sk-proj-...` key; the same key in a `curl` command line; `flamin release`. | **Pass.** Destructive: deny. Key in file: `Secret leak blocked`. Key on a command line: deny, and the audit log line shows `[REDACTED:openai-key]`; the key itself appears nowhere in the audit log. Release: `v1 is closed and a release proposal is ready`, gate `R-0004` waits for the human. |
| **Enforcement layer (D-30)** | Agent write to `kit/engine/flaminlib/policy.py` and to `.claude/settings.local.json`; then a hand edit of `policy.py` committed. | **Pass.** Both writes denied by the hook. The commit failed: `kit file changed and does not match kit/MANIFEST (enforcement layer)`. |
| **Self-approval (D-31)** | `Status: agreed` written into `assumptions.md` by an agent; an agent Write to `.flamin/approvals.json`. | **Pass.** The gate stayed shut; the write was denied (`.flamin/approvals.json is engine state`). |
| **Ask mode (D-32)** | Live Claude Code headless runs asking the Orchestrator to delete `notes.txt` (a gated action) in each permission mode. Codex and Cursor from probes and payload tests. | **Pass.** See §3. In every mode the file survived. |
| **Concurrency: state** | Process A held the state lock for 12 s; process B ran `flamin intake --set name=first`, then retried. | **Pass.** B waited 10 s, then failed cleanly with `Another flamin command is writing state (session s-8594, started 16:29:00). Try again, or run flamin doctor if it looks stuck.` (exit 4). Nothing was overwritten. The retry succeeded. A unit test runs two writers 25 times each: the counter ends at exactly 50. |
| **Concurrency: audit spill (D-37)** | With the lock held, three hook calls wrote audit lines; then one more call after the lock was released. | **Pass.** A `spill-<session>.jsonl` file appeared; after the next append it was merged and removed; all 4 lines are in the log. |
| **Hook health (D-18)** | `flamin status` with no hook call in the last 15 s. | **Pass.** `!!! flamin hooks are not running in this session. Gates are Soft until this is fixed. Run flamin doctor.` In a live Claude Code session with hooks active, the warning did not appear. |
| **Handoff** | A handoff without `## Artifact References`. | **Pass.** `missing section '## Artifact References'` → `Handoff rejected`. Empty sections and each missing section are rejected in unit tests. |
| **Versions** | v1 built, `flamin release`, human `--no`; `cr-new`, `cr-approve v2 --cr CR-001=member@8`, human `--yes`; a CR raised after that; rework; `flamin release`; human `--yes`. | **Pass.** v1 not released and kept in history; v2 opened by the first CR; after approval, member reopened from Step 8 only (steps `[6, 7]`); `CR-002` raised after `cr-approve` landed in v3; build went 1 → 2, one per release candidate; v2 released. `v2.md` holds the CR list, proposal and decision. No D label anywhere in state or version records. |
| **Resume** | Live Claude Code: Step 7 with two specs was interrupted after the first spec was generated (a handoff says so). A new session was told only `continue`. | **Pass.** The Orchestrator ran `flamin status` and `flamin resume`, delegated to the Designer, which designed and generated only the second spec. The first spec's generated files are byte-identical before and after. The step ledger has no duplicate entries. (The first run of this test found a real gap: Step 7 was marked done after the first spec. Fixed: Step 7 is done only when every spec of the module is generated.) |

### Example products

| Profile | Tests | Type check | Result |
|---|---|---|---|
| `python-fastapi` | pytest via FastAPI `TestClient`: register then query by phone, bad input returns the error envelope, duplicate phone returns 409 | n/a | 3 passed |
| `typescript-node` | `node --test`: service tests plus an HTTP test over Express | `tsc --noEmit` clean | 4 passed |
| `javascript-node` | `node --test`: same as above | n/a | 4 passed |
| `java-spring` | JUnit 5 + MockMvc (`mvn test`, Spring Boot 3.2.0) | compiled by Maven | 3 run, 0 failures |
| `typescript-react` | `node --test` on features, adapters and api-client with a fake backend | `tsc --noEmit` clean, pages included | 4 passed |
| `react-native` | same as web | `tsc --noEmit` clean, screens included | 4 passed |
| `minimal` | `unittest` | n/a | OK |

Two real template bugs were found this way and fixed: the web and mobile api-client (data layer) imported the contracts layer, which the Step 10 boundary scan refused; and the TypeScript model repeated the id field, which `tsc` refused.

### Live runs on the real tools (in addition to the table)

- **Claude Code, locked edit by a sub-agent.** The Orchestrator delegated to the Developer. The hook saw `agent_type: developer`; the Developer's edit to `base_register_service.py` was denied with the FULLY_LOCKED message; its edit inside the `validateAggregate` extension went through; the locked file is byte-identical; `check-lock` passed.
- **Codex, rendered adapter.** In a product rendered with `flamin init --tool codex`, `codex exec` ran the project's own `.codex/config.toml` hooks (the exact rendered Windows command `& "$(git rev-parse --show-toplevel)\flamin.cmd" hook pretool --tool codex; exit $LASTEXITCODE`). `git push --force origin main`: blocked (force push). `apply_patch` on `kit/rules/core.md`: blocked (enforcement layer). `rm notes.txt`: blocked, `Approval needed (R-0001)`, terminal path. Nothing ran. The trust review was bypassed with `--dangerously-bypass-hook-trust` for the test; in normal use you trust the hooks in `/hooks`.

## 3. Probe results (logging-only, D-23)

New probes run during the build, in throwaway folders. Each recorded its input and allowed the call, except the deny probes, which only denied their own marker command.

| # | Tool | Question | Result |
|---|---|---|---|
| B1 | Codex | Is `[agents] max_depth` a real key? | **Accepted** by 0.153.4 with `--strict-config` (an unknown key under `[agents]` is rejected). It is **not in the current Codex docs**. `max_threads` is documented as a legacy alias of `max_concurrent_threads_per_session`. Whether `max_depth = 1` stops nesting was not probed; flamin's launch gate also denies a spawn made from inside a sub-agent. |
| B2 | Codex | Can a custom agent be the main thread? (D-36) | **No.** `agent = "..."` and `main_agent = "..."` are rejected as unknown fields; there is no `--agent` flag. The Orchestrator stays the main thread with its instructions in `AGENTS.md`. |
| B3 | Codex | Windows hook key | TOML `command_windows` works (inline hooks). The docs name the JSON key `commandWindows`. A project `.codex/hooks.json` did **not** load in these `codex exec` runs; inline hooks in the project `.codex/config.toml` **did**. |
| B4 | Codex | Does a deny block on Windows? | JSON deny **plus exit code 2** through PowerShell: the hook was reported as **failed and the command ran** (PowerShell turns exit code 2 into 1). JSON deny with exit 0: **blocked**. Exit 2 with `; exit $LASTEXITCODE`: **blocked**. flamin now answers Codex and Cursor with a JSON deny and exit 0, and its Windows hook commands end with `; exit $LASTEXITCODE`. |
| B5 | Codex | `apply_patch` payload (not probed in the design) | `tool_name: "apply_patch"`, `tool_input.command` holds the whole patch text with **absolute** file paths. `permission_mode` is `bypassPermissions` under `codex exec`. |
| B6 | Claude Code | The hook's "ask" per permission mode (D-32), headless `-p` | `default`, `acceptEdits`: the hook answered ask; headless has no human, so Claude Code refused the call (listed in `permission_denials`). `bypassPermissions`, `dontAsk`: the engine denied with the terminal path. `auto`: the engine now denies (see K-6). Whether the interactive prompt reaches a human in an interactive session was **not tested** (needs a person at an interactive session). |
| B7 | Claude Code | Exec-form hooks on Windows (`"command": "python"` plus `args`) | **Work.** Hooks fired in every live run; exit code 2 denies were honoured. |
| B8 | Claude Code | Main session as the Orchestrator (`"agent": "orchestrator"`) | **Works.** The session used the Orchestrator's three-line preview and rules; hook calls from the main session carried `agent_type: orchestrator`. |

### Still unknown (fallbacks stay in place)

| Unknown | Why not tested | Fallback in the build |
|---|---|---|
| Cursor hooks live (payloads, deny through PowerShell, what `{"permission":"allow"}` does to Cursor's own prompts) | Cursor's `agent` CLI is not installed; the desktop app cannot be driven headless from here. | Engine tested against the payload shapes proven in DESIGN §5.4; JSON deny with exit 0; `failClosed: true`; `afterFileEdit` auto-revert. |
| Cursor edit tools on other models (D-26) | Needs a paid plan with model choice. | Any unknown tool with a file path is treated as a write. |
| Cursor reading `.claude/agents/`, `.codex/agents/`, and Claude Code hook files (docs say the CLI does) | Not installable here. | The engine detects a Cursor payload (`cursor_version`) and answers in Cursor's format; launching `orchestrator` is denied. |
| Codex project `.codex/hooks.json` loading | Would need the test project trusted in the user's `~/.codex/config.toml`, which this build must not edit. | Hooks are rendered inline in `.codex/config.toml`, which was proven to load. |
| Codex `max_depth` enforcement, Codex `SubagentStart` model field | Not probed. | The launch gate denies nested spawns (`agent_type` present on the caller). |
| Claude Code interactive "ask" prompt | Needs a human in an interactive session. | Human-only commands also require `--yes` or an ask token that only the hook writes. |
| Linux and macOS | No CI runner. | Sample CI workflow ships with a three-OS matrix. |

### Known gaps (by design, listed so nobody assumes otherwise)

- Hosted tools with no post-hook (for example Codex web search) leave no audit line.
- Shell writes are caught by pattern only (Heuristic). The pre-commit backstop and the Step 12 scan catch the rest.
- Boundary checks read import lines. They cannot see reflection or dynamic paths.
- Agent identity on Cursor comes from the module lease (Heuristic).

## 4. Design versus reality (resolved in review round 5)

The build did not edit DESIGN.md. It reported the items below; the human decided them in review round 5 as D-38 to D-46 (DESIGN.md Appendix A). The table is kept as the record of what the build found. §4a shows how the build was aligned.

| Id | DESIGN says | Reality | What the build does now |
|---|---|---|---|
| K-1 | §4.2 and §6.3: `[agents] max_depth = 1`, `max_threads = 10`, citing the Codex docs | `max_depth` is no longer in the Codex docs but is still accepted by 0.153.4 (probe B1). `max_threads` is a legacy alias. | Renders both keys as designed. |
| K-2 | §4.2, D-29: hooks in `.codex/hooks.json` with `command_windows` | The docs name the JSON key `commandWindows`. Project `hooks.json` did not load in probes; inline hooks in `.codex/config.toml` did (probe B3). §4.2 allows "inline or in `.codex/hooks.json`". | Renders hooks inline in `.codex/config.toml` with TOML `command_windows`. The D-29 re-trust notice now points at `.codex/config.toml`. |
| K-3 | §4.1 names exit code 2 for Claude Code only; nothing for Codex or Cursor | On Windows both run hooks through PowerShell, which turns exit code 2 into 1; Codex then lets the call through (probe B4). | JSON deny with exit 0 for Codex and Cursor; `; exit $LASTEXITCODE` on their Windows commands. Claude Code keeps JSON plus exit 2 (exec form, no shell). |
| K-4 | §4.2: `sandbox_mode` read-only for Business, Analyst, Architect and Planner. §4.3: `readonly: true` for read-only agents | §6.1 gives these four agents paths they must write (`.flamin/business/`, `.flamin/analysis/`, `.flamin/decisions/`, `plan.md`). A read-only sandbox blocks every write. | Renders `workspace-write` for all seven Codex agents and no `readonly` flag on Cursor. Their writes are limited by the per-agent path rules, which are Hard on Codex (`agent_type`). **Decision needed.** |
| K-5 | §15.3 table: Cursor answers gates through its own prompt in `beforeShellExecution` | D-32 uses the tool prompt only in a known interactive mode. Cursor hook input has no permission-mode field (Cursor docs). | Cursor always denies and sends the human to the terminal, as D-32 requires. |
| K-6 | D-32: deny in any bypass or auto-approve mode | The Claude Code docs say a hook's "ask" still forces a prompt in `auto` mode. | Denies in `auto` (the stricter reading). Could be relaxed by a decision. |
| K-7 | DESIGN is silent | Cursor also loads agents from `.claude/agents/` and `.codex/agents/`, and its CLI reads Claude Code hook files and `CLAUDE.md` (Cursor docs; not probed). | Engine answers a Cursor payload in Cursor's format whichever hook file sent it; `orchestrator` launches are denied. |
| K-8 | §2.1 layout does not list `.gitattributes` | Git on Windows (`core.autocrlf`) checks files out with CRLF. That breaks `/bin/sh` for the POSIX launcher and would trip the state checksum check. | Ships a root `.gitattributes` (LF for `flamin`, hooks and state files; CRLF for `.cmd`). Checksums ignore CRLF versus LF. |
| K-9 | DESIGN is silent | Opening the master kit folder itself in Claude Code applies its `.claude/settings.json`: the main session becomes the Orchestrator and the built-in agents are denied. | As designed for products. Kit maintainers edit the kit in another tool or accept this. |

Small build choices inside the design (no decision needed): `flamin handoff --stdin`, because the Orchestrator has no file-write tool on Claude Code; stack approval runs through `flamin intake --stack`, which also serves a change to the product's profile copy; `flamin doctor --kit --update-manifest` rewrites `kit/MANIFEST` for kit maintainers (human-only through the hook).

## 4a. Alignment with D-38 to D-46 (2026-09-26)

| Decision | What changed in the build | How it was checked | Result |
|---|---|---|---|
| D-38 | `.codex/config.toml` uses `max_concurrent_threads_per_session = 10` (plus `max_depth = 1`). `flamin doctor` checks both keys with `codex exec --strict-config`; a local `--oss` provider that is not running stops the run after the config loads, so no model is called. | `flamin doctor --kit` on this machine; unit test of the rendered keys | Codex 0.153.4 accepts both keys. An unknown key under `[agents]` is rejected (seen in the build probe). |
| D-39 | No change needed: hooks were already inline only. The re-trust notice points at `.codex/config.toml`. | Unit test | Pass |
| D-40 | Cursor's Windows command is back to plain `.\flamin.cmd` (JSON with exit 0 decides). `flamin doctor` runs the rendered Codex hook in the shell Codex uses (PowerShell on Windows) with a force-push payload and requires a JSON deny with exit 0. `flamin doctor --probe-codex` runs one harmless command inside the installed Codex and checks the hook heartbeat moved. | `flamin doctor --kit`; `flamin doctor --probe-codex` in a fresh, untrusted product copy | The shell check passes. The live probe correctly reported "no flamin hook ran inside codex-cli 0.153.4" for the untrusted copy, with the fix (trust the project and hooks). With trust bypassed for one run, Codex blocked the force push, the `apply_patch` to `kit/rules/core.md` and the delete (gate R-0001). |
| D-41 | The hook denies every shell call from Business, Analyst, Architect and Planner (Claude Code and Codex, from the agent identity in the hook). Soft on Cursor, which sends no identity. | Unit tests for all seven agents and the main session, on both tools | Pass |
| D-42, D-43 | No change needed: Cursor gates and Claude Code `auto` already deny and send the human to the terminal. | Existing unit tests | Pass |
| D-44 | Each tool call has one key: `tool_use_id`, else `generation_id` + hook event + a hash of the tool input. The audit log writes one line per key (the last 2000 keys are kept in `.flamin/tmp/calls.json`, under the state lock). A repeated call reuses its open gate request, and an approved action stays allowed when the same call arrives again; a new call needs a new answer. | Unit tests: a Cursor payload sent through the Cursor and the Claude Code hook files; the approval repeat | Pass. The live Codex run now writes one audit line per blocked call (the first build wrote two for some calls). |
| D-45 | `.gitattributes` joined the enforcement layer; its state rule is `.flamin/**/*.json`. `kit/githooks/*` are committed as mode 100755. `flamin doctor` flags a launcher or hook template without the executable bit. The master kit now has its own `.gitignore` (the DESIGN §2.7 entries). | Unit tests; `git ls-files -s` | Pass |
| D-46 | New `flamin kit-maintenance on/off/test/status`. On and off are human-only through the hook (as is `flamin doctor --update-manifest`). With the flag and no `.flamin/`, agents may write `kit/` and the launchers; tool config, rules files, `.gitattributes` and `.git/` stay shut. `flamin init` refuses and `flamin doctor --kit` fails while the flag exists. `flamin kit-maintenance test` runs the engine tests, refreshes `kit/MANIFEST` and records it; `check-staged` refuses kit changes in maintenance mode unless the staged manifest is the tested one. | Unit tests | Pass |

Not done, because DESIGN.md leaves it open: how a maintenance session gets file-editing tools (D-46), and P-27 (not answered in round 5).

Noted during alignment: `.codex/config.toml` had been rewritten outside the build (a `[shell_environment_policy]` block with two Claude Code variables, which Codex does not read); the re-render replaced it. `.codex/agents/orchestrator.toml` had also appeared; D-36 renders only the seven workers, so it was left untouched and not committed. `flamin doctor` in a product writes one audit line for its own hook self-check (session `flamin-doctor`).

## 4b. Product tool selection (D-47, 2026-09-28)

| Check | How | Result |
|---|---|---|
| `flamin init` inside Claude Code renders only Claude Code | Fresh copy of the kit, `flamin init` run from a Claude Code shell (`CLAUDECODE=1`) | `Adapters for claude (detected: Claude Code)`; no Codex ACTION line; `state.json` records `tools: ["claude"]` |
| `flamin doctor` checks only Claude Code | Same copy | Settings files current; the PreToolUse hook, run exactly as Claude Code runs it, denies a force push (JSON deny, exit 2); `Codex: not used by this product; checks skipped.`; no blockers |
| Cursor checks in `flamin doctor` (added 2026-09-28) | Master kit `flamin doctor --kit`; Cursor product in unit tests | Adapter files current; the rendered `preToolUse` command, run through PowerShell from the project root, returns `{"permission": "deny"}` with exit 0; `Cursor: not used by this product; checks skipped.` on other products. Shell-level only: Cursor itself was not run (its CLI is not installed here). |
| Unit tests | `kit/engine/tests/test_tools.py` (14 tests): detection, default, `--tool all`, adding a tool, doctor never calls Codex on a Claude-only product, legacy product without `tools`, broken hook reported, prune gated and rejected, CRLF checkout counts as current | Pass (135 in total) |

Not verified: Codex and Cursor auto-detection. Their docs name no marker for the agent's own shell, so both must be named with `--tool`. A kit copy still carries the shared Codex and Cursor files; `flamin doctor --fix --tool claude --prune` removes them through the delete gate.

### D-48 exclusive tool selection (2026-10-02)

D-48 supersedes the multi-tool and prune behavior recorded above. `flamin init` selects one tool and archives all other discoverable adapters under `.flamin/inactive-adapters/`. An existing product switch opens a `tool-switch` request; only a human terminal answer allows it. The tests in `kit/engine/tests/test_tools.py` cover copied master adapters, all three selections, rejected and approved switches, preservation of customized files, doctor repair and override blocking, wrong-tool hooks, and ambiguous legacy state. `flamin kit-maintenance test` is the release check for these changes.

The required `flamin kit-maintenance test` gate passed and recorded the tested kit manifest at **00:25:24 on 2026-10-03 (Malaysia time)**. A read-only check found 147 tests in the engine suite, confirmed that the recorded SHA-256 and current `kit/MANIFEST` SHA-256 both equal `f113c8f0c7225bc7a42042277779cfb32fd2ca5bf7c3113330d012105256c2e0`, and found no kit file mismatches (`manifest.verify_tree()` returned `[]`). The read-only check did not rerun the suite. Live Cursor behavior and Linux/macOS execution remain unverified, as described in §3.

## 4c. Codex shell environment alignment (2026-10-02)

The local shell environment policy is now part of the canonical Codex adapter.
Correction to the earlier alignment note: Codex reads the shell policy and
passes its `set` values to subprocesses; the Claude variables do not configure
Codex agent limits. `inherit = "core"` restricts inherited variables, so projects
relying on custom environment must configure it explicitly.

The renderer test parses generated TOML and checks the shell policy, native
agent limits, all five unchanged hook groups, and exactly seven worker agents.
The main chat remains Orchestrator; the local extra Orchestrator file is outside
this change. Generated and local configuration are compared semantically,
ignoring comments and formatting. Subprocess behavior in a live Claude session
is not tested.

Validation: focused `test_round5.py` suite passed 11 tests in 21.077s;
`flamin kit-maintenance test` passed all 136 tests in 292.280s and refreshed
`kit/MANIFEST`. The generated configuration semantically matches the local
TOML exactly. `git diff --check` passed.

## 5. Name check

The forbidden-name check from the build task (D-25; the pattern lives only in the build task) over file contents and file names inside the kit folder, excluding `.git`: **0 matches** at the end of the build (see BUILD_LOG.md). Commit messages: none were made during the build.

## 6. Master kit cleanliness

`flamin doctor --kit` at the end of the build: no `.flamin/`, no product code, no audit logs, no caches (`__pycache__` is never written: the engine sets `sys.dont_write_bytecode`), no secrets, no machine-local hook files, kit manifest clean, no absolute paths or user or machine names in kit files.

## 7. Master-kit v1 distribution candidate (2026-10-03)

`flamin kit-maintenance test` passed all 147 engine tests in 278.019 seconds and refreshed `kit/MANIFEST`. The release builder verified that manifest before producing `flamin-v1.zip`, `flamin-v1.tar.gz`, `flamin-v1-npm.tgz`, and `flamin-v1-SHA256SUMS.txt` locally. Focused distribution tests checked deterministic archive bytes, matching ZIP and tar member lists, exclusion of the machine-local Cursor hook and untracked adapter, npm package name/version, and rejection of a changed kit file. `npm pack --dry-run --offline --ignore-scripts` recognized `@peterkoay/flamin@1.0.0` with 205 files. These checks do not establish that GitHub publication has occurred; the tag workflow performs the cross-platform checks before publishing.
