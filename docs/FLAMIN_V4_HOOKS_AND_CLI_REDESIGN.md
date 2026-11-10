# FLAMIN v4 hooks and terminal interface redesign

**Review and implementation handoff for ChatGPT Work | 10 November 2026**

Source workspace `C:/Users/WJKWEEJI/Code/TocoAI/flamin_v3_Claude`. Proposed implementation branch `flamin_v4`. Start from a baseline the owner selects after inspecting the existing changes. Keep this handoff outside the repository. It is disposable after the decisions are recorded in the project.

## Intent

Make two coordinated changes to the master kit.

1. Remove FLAMIN-installed tool hooks for Codex, Claude Code, and Cursor, plus its Git pre-commit hook. Redesign agent rules in Markdown and keep verifiable controls at the points where work is accepted.
2. Remove the normal FLAMIN terminal workflow, including Windows `flamin.cmd`, POSIX `flamin`, and the general-purpose CLI. Users work through chat and an explicit human decision surface. Agents use typed engine operations. CI uses a narrow machine entry point.

The cross-platform scope and the machine-only exception are proposed interpretations. Confirm them before implementation. Preserve the engine's ownership of product state, step transitions, generation, and leases. Preserve existing product artifacts, completed work, and decisions.

## Second-pass findings

| Finding | What the redesign needs |
| --- | --- |
| Windows tool hooks and the Git hook call `flamin.cmd`. The POSIX launcher and direct Python entry point share the parser. Product CI already calls Python directly. | Remove live registrations before deleting their targets. Migrate every product, maintenance, and CI caller before retiring the CLI. |
| The status heartbeat warning is a mandatory stop in the Orchestrator instructions. `doctor` probes, diagnoses, and repairs hook wiring. | Remove the startup stop condition, heartbeat, trust instructions, probes, and hook repair together. Keep diagnosis of actual tool availability, state integrity, and migration readiness. |
| The old upgrader loads its renderer before replacing kit files. It then renders with the code it already loaded. Normal rendering does not delete obsolete files, and adapter isolation can restore archived configurations. | Use a migration that runs target-version code. Test upgrade launched from an old kit. Strip retired FLAMIN registrations from active and restorable configurations while preserving unrelated settings and hooks. |
| The product approval path can label an explicit `--yes` as a human terminal decision. Some other paths rely on caller environment markers. | Separate agent proposal from human authorization. Use a host form to request human confirmation. Bind the decision to the exact workspace, proposal, and relevant artifact revisions. Reject stale or replayed approvals. |
| Diff verification mixes staged or committed data with working-tree reads. A changed lock record can match changed generated code. The sample CI ignores `doctor` failure. | Verify the exact staged index or commit range against trusted base data. Distinguish genuine regeneration from edits to both the locked code and its lock record. Make required checks fail CI and prevent proposed changes from redefining the verifier that judges them. |
| Removing pre-commit removes automatic local verification. Existing maintenance authorization binds to staged contents and HEAD. Range verification skips the local maintenance test gate. | Decide whether to add a controlled maintenance commit action that rechecks the approved index or require authorization evidence at merge or publication. A raw Git commit can still happen where host permissions allow it. Preserve full, targeted, and skipped validation as distinct outcomes. |
| The master working tree and TestPeteDemo reported the same `v2` kit label, but their generated Codex matchers differed. The master tree also had pre-existing kit edits. The historical Business D-41 denial cannot identify which live adapter or version handled the call. | Detect installed files and state format as well as version. Inspect the current Git state and confirm the branch baseline before changing anything. Use TestPeteDemo as an acceptance case, without assuming its current runtime state. |

## Change 1 - Replace live hook controls

- Use `kit/rules/core.md` for shared agent rules and `kit/agents/` for role-specific ownership. Render instructions consistently across the supported hosts. Assign each worker the active step, owned paths, expected artifact, and completion evidence.
- Let agents read relevant context and make authorized in-scope edits without a per-edit FLAMIN request. For a deletion, external change, dependency change, paid action, or credential change, have the Orchestrator show the exact scope and ask the human once. Renew consent when material scope changes.
- Separate intake answers, operational consent, and formal engine decisions. A Business agent can record an answer as part of intake. Approving an assumption, stack, baseline amendment, release, or maintenance waiver requires the formal decision route.
- When an action fails, report the operation and actual reason and continue independent work. The Orchestrator supplies file contents if the owner lacks a reader. If the owner cannot save the artifact, return a proposed diff and keep the step open. Verification should point to the violated rule and next action. Retire automatic edit reversal.
- Keep host sandbox and connector permissions. Markdown guides agent conduct. It cannot prevent a process with write access from editing protected files, verify a person's identity, or undo a secret leak or external side effect. Check generated locks, boundaries, phase order, secrets, and integrity when work is submitted, at review, and in CI.
- Once replacements work, remove hook registrations, dispatch and response formats, heartbeat files and warnings, shell classifiers used only by hooks, post-tool approval inference, Git hook installers and templates, and hook-only tests. Retain shared verification and decision audit logic. Move session reminders to explicit handoff and close-out.

## Change 2 - Replace the terminal workflow

The current parser is the command inventory. The replacements below describe functions, so a typed operation can replace several verbs.

| Existing commands | Replacement |
| --- | --- |
| `status`, `resume`, `audit` | Read workspace status, next eligible role, last handoff, pending decisions, and recent outcomes. Reading does not advance the workflow. |
| `init`, `upgrade`, `doctor`, `rebuild-locks` | Structured setup, migration, diagnosis, and recovery. Separate previews from repairs. Rebuild lock records from trusted generation evidence or a known-good baseline. |
| `intake`, `analyze`, `model`, `baseline-review`, `baseline-amend`, `plan`, `design`, `cr-new`, `cr-approve`, `release` | Typed submissions validate the active step and artifact revision, then record the transition or open a decision. Keep release proposal, approval, and external publication separate. |
| `generate`, `develop`, `wire`, `module-done`, `integration-test`, `architecture-review` | Engine operations for generation, lease acquisition and release, wiring, and verification. Bind evidence to the revision checked. Handle concurrency, retries, and partial failure. |
| `lint`, `check-syntax`, `check-lock`, `check-boundary`, `check-phase`, `check-staged`, `handoff` | Shared checks with explicit working-tree, staged-index, or commit-range inputs. Keep handoffs in Markdown and validate them when submitted. |
| `approve`, `kit-maintenance` | Human-controlled actions with distinct agent-facing read and proposal operations. Preserve validation choice, reason, historical decisions, and exact scope. |
| `hook`, `doctor --probe-codex` | Retire. |

Use an explicit workspace identity and typed operations. Avoid exposing arbitrary shell or a generic command proxy. Prove tool discovery and fresh setup on each supported host. A missing tool or approval surface should show a specific blocker and leave state intact. Update generated rules, manifest, packaging and archive tests, path classification, release workflow, and CI with the selected entry points.

## Migration and acceptance

1. **Prove the replacements first.** Build the structured workflow operations, human approval route, and snapshot verifier. Agree on local maintenance and trusted CI policy before removing the pre-commit gate.
2. **Preview the workspace migration.** Inventory live and archived adapter configuration, Git hook locations, launchers, CI callers, and pending requests. Preserve formal workflow decisions and history. Supersede obsolete hook-only requests without replaying approvals. Back up affected files and make migration resumable and safe to repeat. Preserve user-owned hooks, mixed settings, and custom Git hook configuration.
3. **Pilot TestPeteDemo.** Open the migrated workspace and say “continue.” Business reads and updates its owned intake Markdown. The Orchestrator records the answers and reports missing fields. Formal assumption decisions appear separately. Restart and confirm progress persists. The user needs no hook trust, per-edit request ID, or FLAMIN terminal command.
4. **Exercise the failure paths.** Reject out-of-phase submissions, unauthorized generated-code and lock-record changes, invalid cross-module imports, stale approvals, and stale leases. Accept a valid extension edit. An unrelated working-tree change should not affect the same staged or commit verification result. Test interruption, repeated migration, upgrade from older copies, and release contents on the supported OS matrix.

## Decisions to settle before implementation

1. Confirm whether retirement covers all interactive FLAMIN CLI use across platforms, with a narrow machine-only exception for CI and migration.
2. Use Codex first and request formal human decisions through its confirmation form.
3. Decide whether maintenance requires a controlled local commit action or whether required verification before merge or publication is sufficient.
4. Decide which installed versions must migrate directly, how long a migration bridge remains, and how projects without required CI are handled.

## Source map

Paths below are relative to the source repository.

- `flamin.cmd`, `flamin`, `kit/engine/flamin.py`, and `kit/engine/flaminlib/cli.py` for dispatch and command inventory.
- `kit/engine/flaminlib/hooks.py`, `policy.py`, `render.py`, and `kit/githooks/` for hook behavior and installation.
- `kit/engine/flaminlib/cmds.py` for init, status, doctor, upgrade, and the diff backstop.
- `kit/engine/flaminlib/flow.py`, `approvals.py`, `maintenance.py`, and `commit_validation.py` for state transitions and approval provenance.
- `kit/engine/flaminlib/locks.py`, `statefile.py`, and `profiles.py` for lock, state, and snapshot concerns.
- `kit/rules/core.md`, `kit/agents/`, and `kit/adapters/` for canonical and rendered instructions.
- `kit/ci/flamin-check.yml`, `.github/workflows/release.yml`, `kit/MANIFEST`, `tools/build_release.py`, and the engine and packaging tests for distribution and verification.

This handoff is based on source inspection. No FLAMIN commands, model probes, migration, or behavioral tests were run. No branch was created.
