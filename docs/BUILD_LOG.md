# flamin build log

Build handoffs for the Stage 3 build of the master kit. Same three-section format as a product handoff (DESIGN §6.6).

---

## Build handoff 1 (2026-09-25)

## Objective
Build the working flamin master kit from the approved DESIGN.md (D-01 to D-37), in the order of the build task (items 1 to 13), and prove it with the acceptance tests. Done means: every build item exists and is tested, VERIFICATION.md reports real results, the master kit is clean, and any clash between design and reality is reported instead of changed.

## State Ledger
- Completed: 1 core engine (state, file lock, checksums, locks, modules, approvals.json, stack profile loading, versions, every check command) with unit tests
- Completed: 2 seven stack profiles with generator templates (java-spring, python-fastapi, typescript-node, javascript-node, typescript-react, react-native, minimal)
- Completed: 3 flamin-generate and flamin-lint (spec checks, drift, portability)
- Completed: 4 hooks: PreToolUse (destructive, secrets, state files, enforcement layer, human-only commands, lock, boundary, phase, launch gate), audit logging (redaction, spill file, retention), Approval Gates (interactive-mode check, ask tokens, terminal path), hook heartbeat
- Completed: 5 git pre-commit hook (POSIX and Windows), kit/MANIFEST, sample CI workflow with `check-staged --range`
- Completed: 6 eight tool-neutral agent definitions and prompts in kit/agents/, plus models.json
- Completed: 7 Claude Code adapter; 8 Codex adapter; 9 Cursor adapter (see decisions pending)
- Completed: 10 `flamin init --tool <claude|codex|cursor|all>` and the plain-language start ("start a new project")
- Completed: 11 doctor, status, resume, audit, approve, cr-new, cr-approve, release, upgrade
- Completed: 12 logging-only probes for the remaining Unknowns (VERIFICATION.md §3)
- Completed: 13 README.md, WORKFLOW.md, VERIFICATION.md, this log
- Completed: acceptance tests (VERIFICATION.md §2): 111 unit tests OK; seven example products generated and tested; live Claude Code and Codex runs
- Current: waiting for the human to decide the design-versus-reality items K-1 to K-9 (VERIFICATION.md §4)
- Pending: a review round for K-1 to K-9 (decisions from D-38); Linux and macOS runs through the sample CI job; live Cursor probes once the Cursor CLI is available
- Decisions: none new. DESIGN.md was not edited. Build choices inside the design are listed in VERIFICATION.md §4.
- Open questions: K-4 (read-only sandbox for four agents that must write their docs) needs a decision before the Codex and Cursor adapters are final

Name check (forbidden-name pattern from the build task, over file contents and names inside the kit folder, excluding .git): 0 matches.

## Artifact References
- Changed: `flamin`, `flamin.cmd`, `.gitattributes`, `kit/**`, `CLAUDE.md`, `AGENTS.md`, `.claude/settings.json`, `.claude/agents/*.md`, `.codex/config.toml`, `.codex/agents/*.toml`, `.cursor/agents/*.md`, `docs/README.md`, `docs/WORKFLOW.md`, `docs/VERIFICATION.md`, `docs/BUILD_LOG.md`
- Not changed: `docs/DESIGN.md` and the three read-only guides
- Must read: `docs/DESIGN.md`, `docs/VERIFICATION.md` §4 (clashes), `kit/engine/flaminlib/hooks.py` (every tool's reply format), `kit/engine/flaminlib/render.py` (adapters)
- Tests: `python -B -m unittest discover -s kit/engine/tests -t kit/engine/tests`

---

## Build handoff 2 (2026-09-26)

## Objective
Align the build with review round 5 (DESIGN.md D-38 to D-46). Done means every decision is implemented or confirmed, tested, recorded in VERIFICATION.md §4a, and the master kit is clean.

## State Ledger
- Completed: D-38 Codex `[agents]` keys and the `--strict-config` doctor check
- Completed: D-40 Cursor launcher, doctor hook-shell check, `doctor --probe-codex`
- Completed: D-41 shell denied for agents without a shell tool
- Completed: D-44 one audit line and one gate answer per tool call
- Completed: D-45 `.gitattributes` in the enforcement layer, executable bits, master-kit `.gitignore`
- Completed: D-46 `flamin kit-maintenance`, init refusal, doctor --kit failure, tested-manifest check at commit
- Completed: D-39, D-42, D-43 confirmed (no change needed)
- Completed: 121 unit tests OK; `flamin doctor --kit` clean; live Codex gates re-run
- Current: waiting for the human to review and commit
- Pending: how maintenance sessions get file-editing tools (open in D-46); P-27 (not answered); Linux and macOS CI; live Cursor probes
- Decisions: none new; DESIGN.md changed only in the human's review round
- Open questions: `.codex/agents/orchestrator.toml` appeared outside the build; keep or delete (D-36 says seven workers only)

## Artifact References
- Changed: `kit/engine/flaminlib/{audit,cli,cmds,hooks,policy,render}.py`, `kit/engine/tests/test_round5.py`, `kit/adapters/codex/config.toml`, `kit/rules/core.md`, `kit/githooks/*` (mode), `kit/MANIFEST`, `.gitattributes`, `.gitignore` (new), `.codex/config.toml`, `CLAUDE.md`, `AGENTS.md`, `docs/README.md`, `docs/WORKFLOW.md`, `docs/VERIFICATION.md`, `docs/BUILD_LOG.md`
- Must read: `docs/DESIGN.md` Appendix A (D-38 to D-46), `docs/VERIFICATION.md` §4a

---

## Build handoff 3 (2026-09-28)

## Objective
Kit maintenance (D-46 mode on): flamin renders, checks and warns only for the AI tool(s) a product uses, for new and existing products. Done means `flamin kit-maintenance test` passes and a fresh kit copy initialised in Claude Code shows only Claude Code adapters and checks.

## State Ledger
- Completed: `state.json` `tools`; `flamin init` tool detection (Claude Code only) with default Claude Code; `--tool` adds, never removes
- Completed: `flamin doctor` Claude Code checks, Codex checks only for Codex products, `--fix` per product tool, `--prune` through the delete gate
- Completed: status hook warning, stale-adapter check and `flamin upgrade` limited to the product's tools
- Completed: rendering treats a CRLF checkout of the same text as current (no false re-render or Codex ACTION note)
- Completed: D-47 in DESIGN.md (human-issued kit maintenance request); README; 132 unit tests OK
- Current: waiting for the human to review, commit and turn maintenance mode off
- Pending: Codex and Cursor auto-detection (no documented marker)
- Decisions: D-47
- Open questions: `.codex/config.toml` and `.codex/agents/orchestrator.toml` were rewritten again on 2026-09-27 outside flamin (see the session report)

## Artifact References
- Changed: `kit/engine/flaminlib/{cmds,render,cli,flow}.py`, `kit/engine/tests/test_tools.py`, `kit/MANIFEST`, `docs/DESIGN.md`, `docs/README.md`, `docs/VERIFICATION.md`, `docs/BUILD_LOG.md`
- Must read: `docs/DESIGN.md` D-47, `docs/README.md` "Which AI tool a product uses"
## Private GitHub publication preparation (2026-10-02)

- Target: `peterkoay/flamin`, private for human review before public release.
- Added a root README with setup and documentation links.
- Added the MIT license, copyright 2026 Peter Koay, as selected by the human.
- Configured `origin` as `https://github.com/peterkoay/flamin.git`.
- Validation: `flamin kit-maintenance test` passed all 135 tests.
- Local changes to `.codex/config.toml` and the untracked
  `.codex/agents/orchestrator.toml` remain untouched and excluded from the
  publication commit. See the adapter rewrite note in `VERIFICATION.md`.
- The local maintenance flag is ignored by Git and is not shipped.
