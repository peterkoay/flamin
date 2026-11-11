# FLAMIN v4 without a terminal

Operator workflow and implementation contract. The development checkout supports the product workflow in Codex from intake through the local release decision. The setup and rollout sections include planned work.

## Codex workflow

The workspace-bound MCP server exposes status, continuation, review of formal proposals, and confirmed intake saving. Use the tool for each workflow step after its artifacts are ready. The server records requirements, models, plans, designs, generation, development, wiring, test results, architecture checks and change requests.

The copy-only migrator recognizes the installed v2 Codex layout before stack approval. Its preview binds the rehearsal to a source fingerprint. It publishes a separate copy only after checking the captured files, removing identified Codex hooks and validating state checksums. It preserves request payloads and marks obsolete Codex shell and edit requests superseded in the copy. It preserves Git metadata and the Git backstop. Customized rules, unknown hook wiring and outstanding formal requests require review before applying this rehearsal.

Maintainers can preview or create the copy with `kit/engine/flamin_rehearse.py`. The source path is required. Creating a copy also requires `--copy-to` and the preview's `--expected-snapshot`. Pair the MCP server `kit/engine/flamin_intake_mcp.py` to the absolute path of the copy.

For a fresh product folder containing the kit, pair the same server and use `flamin_initialize` to confirm creation of the engine state and intake documents. The initializer writes product state and leaves host configuration to the pairing step.

### Human confirmation

Use `flamin_confirm_decision` for assumptions, shipped stack proposals, baselines, baseline amendments, plans, release proposals and change lists. The host shows the proposal and the human confirms the choice. FLAMIN checks the reviewed scope again and applies the outcome through the existing workspace engine. A confirmed stack approval makes Step 1 ready.

### Snapshot verification

Use `flamin_verify_snapshot` in Codex or run `kit/engine/flamin_verify_snapshot.py` in CI. Both take a full trusted base commit ID. The verifier accepts ordinary edits that pass its checks and flags changes to engine records, the kit, and generated structure for separate review.

For a shared GitHub product repository, first put the FLAMIN v4 verifier on the base branch. Copy `kit/ci/flamin-check.yml` to `.github/workflows/flamin-check.yml`. It runs one job per pull request and checks the proposed merge against its base using the verifier from the base commit. After the first successful run, the repository owner can require the **FLAMIN snapshot** check before merge. Review workflow changes as part of the pull request. Repositories that enforce verification through a centrally managed workflow can use `kit/ci/flamin-protected-snapshot.yml`.

## What you do in chat

Open the workspace in a supported host. A graphical setup screen pairs it with FLAMIN and registers the workspace tools. A person can complete setup, migrate an existing workspace, and recover an interrupted migration through that screen.

| You say | FLAMIN does |
| --- | --- |
| “Start a new project.” | Previews setup for the paired folder. After confirmation, initializes a clean workspace and starts Business intake. If a product already exists, shows its name and asks you to choose a different folder for the new product. |
| “Continue.” | Shows the saved step, eligible role, missing inputs, and outstanding decisions. Resumes from the next eligible action. |
| “Here are the missing intake answers.” | Gives the exact answers to Business to record in its owned Markdown. Checks which required fields remain. Opens formal proposals separately when the workflow requires them. |
| “Show me what needs my approval.” | Shows pending proposals and opens the host confirmation form. The form displays the action, target, exact revision, and effects before you decide. |
| “Check this work.” | Reports results for the requested worktree, staged snapshot, or commit. Shows the checked revision and points to the rule and file for each failure. |
| “Prepare this kit change for commit.” | Previews the staged files, manifest, and validation options. A controlled commit action uses the reviewed snapshot and the recorded validation choice. |
| “Prepare the release.” | Validates a candidate and opens a release proposal. Publication has its own scoped confirmation. |

The Orchestrator previews consequential work using **Understood**, **Planned**, and **Because**. It names the responsible role and operation in plain language. If a tool, confirmation form, or role capability is missing, it reports the specific blocker and continues any independent work. An owner who cannot save an artifact returns a proposed diff and leaves the step open.

## How the kit runs

The host starts the workspace-bound FLAMIN MCP server and invokes typed operations. The server validates inputs and runs the engine in the paired workspace. Each operation declares its input, result, and whether it mutates state. The service exposes neither arbitrary commands nor a generic wrapper around the old parser.

`flamin.md` is the operator guide. Shared agent instructions come from `kit/rules/core.md`, with role instructions from `kit/agents/`. The renderer emits each host's Markdown instructions and assigns the eligible role its step, owned paths, artifact, and completion evidence. Agents use native editing tools for ordinary in-scope changes. Host permissions still govern file, process, and connector access.

The engine keeps state, phase order, leases, generation, and gate outcomes in the workspace. The host mediates human confirmation and the engine records the decision in the existing ledger.

| Operation family | Engine behavior | CLI coverage |
| --- | --- | --- |
| Inspect workspace | Return product status, active step, eligible roles, last handoff, pending decisions, and audit results. Reads leave the workflow position unchanged. | `status`, `resume`, `audit` |
| Set up and diagnose | Preview setup and migration separately from applying them. Diagnose tool discovery, state integrity, and migration readiness. Lock recovery requires trusted generation evidence or a known-good baseline. | `init`, `upgrade`, `doctor`, `rebuild-locks` |
| Submit workflow artifacts | Check role, active step, expected state revision, and artifact digest. Record the transition or return a typed proposal for the human. | `intake`, `analyze`, `model`, `baseline-review`, `baseline-amend`, `plan`, `design`, `cr-new`, `cr-approve`, `release` |
| Generate and deliver | Bind generation to approved inputs. Issue revision-bound module leases, validate wiring and tests, and accept delivery against the checked snapshot. | `generate`, `develop`, `wire`, `module-done`, `integration-test`, `architecture-review` |
| Verify and hand off | Check a named snapshot and return actionable findings. Validate Markdown handoffs when submitted and record explicit session close-out. | `lint`, `check-syntax`, `check-lock`, `check-boundary`, `check-phase`, `check-staged`, `handoff` |
| Manage decisions and maintenance | Agents can propose and read outcomes. The human controls formal decisions and maintenance mode through host confirmation. | `approve`, `kit-maintenance` |

Mutations carry an expected state revision and request ID. The engine serializes conflicting writes per workspace and module. The same request ID and payload return the recorded outcome after retry. Reusing an ID with a different payload fails. Work submitted against an expired, released, or superseded lease fails before state advances. Multi-file generation and migration journal their changes so a restart can finish or recover the operation without a second transition.

## Where the human decides

Intake answers are recorded in chat. Ordinary operational consent, such as authorizing a specified deletion or external connector action, can also be given in chat after the Orchestrator shows the exact scope. A material scope change requires renewed consent. Host or connector permission checks still apply.

Formal engine decisions use the host confirmation form. FLAMIN displays the action and its scope, then checks the proposal and revision again before applying the human's response. A declined or cancelled form leaves the decision open. A human who confirms a rejection records a rejection.

For a maintenance commit, the approved scope includes current HEAD, the exact staged tree including file modes and the generated manifest, and the validation choice and reason. The UI shows **full suite passed**, **targeted checks passed** with the named checks and results, or **validation skipped** with the human's reason. The service rechecks the approved tree and atomically updates the intended ref against the expected HEAD. A changed tree or HEAD requires a new proposal. A raw Git commit may still be possible where the host permits it. Protected merge and publication require verification of the submitted commit and, where applicable, valid maintenance evidence bound to that commit.

## What replaces the hooks

Enforce workflow checks when artifacts are accepted, at architecture review, and before merge or publication. Run lock, boundary, phase, state integrity, and secret checks against one immutable snapshot. Worktree checks capture the revision they read. Staged checks resolve the index to a tree. CI verifies the candidate against a trusted base commit using only those commits' trees and explicit policy inputs. Unstaged edits cannot influence an index or commit check.

Validate generated code against lock records from the trusted base. A legitimate change to a locked section needs reproducible generation from approved inputs using trusted generator code, or independently verifiable engine evidence that binds those inputs to the new output. Changing generated code and its lock record together does not satisfy the check. Validate allowed extension edits and cross-module imports against that same snapshot.

Run required checks from a protected workflow and a pinned verifier outside the candidate's control. Candidate engine tests may run separately in an isolated job with no approval credentials. Required integrity failures fail CI. Protect the merge and release routes that rely on those checks. Local-only workspaces can use the installed verifier for acceptance and controlled commits. They report that remote merge enforcement is unavailable until a protected check is configured.

After the replacements pass acceptance, remove the FLAMIN tool and Git hooks, heartbeat and startup stop, hook repair and probes, automatic edit reversal, hook-only shell classification and approval inference, and obsolete hook tests. Keep evidence and audit logic. Retire interactive FLAMIN launchers and parser on every supported platform. Keep a narrow machine entry point for snapshot verification in CI and a temporary internal bridge for migration. Neither can approve decisions.

## Rollout and exit checks

1. **Prove the new route in Codex first.** Use a clean test workspace. Complete graphical pairing, discover typed tools, submit work, approve through the host confirmation form, restart, and continue without a terminal. Verify that the form is required and that cancellation, changed proposals and replay leave decisions unapplied. Prove each additional host before offering it as supported.
2. **Prove verification and maintenance.** Accept a valid extension edit and trusted regeneration. Reject out-of-phase work, stale leases, illegal imports, and lock-record tampering. Check that a working-tree edit leaves the result for the same staged or committed snapshot unchanged. Demonstrate full, targeted, and skipped maintenance validation, then show that a changed HEAD or staged tree invalidates the approval. Show that a candidate cannot weaken the required CI check that judges it.
3. **Preview and migrate a copy.** Detect installed layout, state schema, active and archived adapters, Git hook paths, CLI callers, and pending requests. Use the installed target version's migrator. Show the exact files and registrations affected before approval. Back up affected files and preserve unrelated settings, user hooks, and custom Git hook paths. Remove only identified FLAMIN registrations, including restorable copies. Flag ambiguous mixed hooks for human review. Preserve formal history, reissue unresolved decisions that need the new provenance, and mark hook-only requests superseded. Verify interruption recovery and idempotence before changing the original workspace.
4. **Pilot TestPeteDemo.** Confirm its actual starting state. Open the migrated copy and say “continue.” Business can read and edit its owned intake Markdown. The Orchestrator checks missing fields and surfaces formal assumption proposals separately. Restart and confirm progress persists.
5. **Retire the legacy path.** Migrate callers before removing launchers and the parser. Verify fresh install, migration, CI, and release archives on Windows, macOS, and Linux. Release only after active and restorable configurations have no retired FLAMIN registrations and supported workspaces can reach every workflow step without a terminal.

## Proposed defaults and implementation gates

- Codex is the first host to prove. Claude Code and Cursor follow after equivalent tests. Verify host registration and the human confirmation form before hook removal.
- Use both a controlled local maintenance commit and a protected CI check for merge and publication. The local action covers offline kit work. CI covers enforcement where direct Git operations are possible.
- Target the installed v2 layout first and require positive format detection even when the version label matches. Preview older and unknown layouts read-only until fixtures prove a migration path. Keep the migration bridge through the pilot and supported-layout tests, then remove it once supported installs can migrate with the graphical installer.
- Choose the implementation baseline after the branch and pending changes can be inspected. Hook and launcher deletion is a separate rollout action once replacement evidence exists.
