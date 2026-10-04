## What this project is

This folder uses **flamin**: a team of AI agents builds software through a fixed flow, and a small engine keeps the flow honest. The engine owns all state in `.flamin/` and all checks. Run it as `./flamin <verb>` (Linux, macOS), `.\flamin.cmd <verb>` (Windows PowerShell) or `./flamin.cmd <verb>` (Windows Git Bash).

**Master kit.** If `flamin status` says "Master-kit maintenance mode is ON", this is kit maintenance, not a product: the product flow does not apply. The main assistant handles kit and similar infrastructure changes directly, including rules, engine, launchers and adapter sources; never delegate this work to the seven product agents. The main assistant also handles initial kit creation before those agents exist. Only a human turns maintenance mode on or off (D-46). The human chooses the validation required for a maintenance commit: the full suite, targeted tests, or explicitly skipping tests. Follow that choice; never run the full suite after the human requests targeted tests or no tests. Targeted validation must identify the checks that ran and their results. Skipped validation must be recorded as skipped, never as a test pass. The engine must provide a human-only approval route for waiving the full-suite requirement, bound to the workspace, current HEAD and exact staged contents. Changes to that scope invalidate the waiver. The waiver applies only to the authorized commit; other checks and release approvals remain active. If the engine does not support the requested route, report the limitation and stop the commit without bypassing hooks.

## Where things are

- `.flamin/state.json` and the other `.flamin/*.json` files: engine state. Read with `flamin status`, never edit.
- `.flamin/business/`: overview, glossary, actors (with permissions), journeys, rules, states.
- `.flamin/analysis/<slug>/`: requirements.md, plan.md.
- `.flamin/technical/`: overview, capabilities.
- `.flamin/design/<module>/`: model.toml (data model) and one TOML spec per feature (generator input).
- `.flamin/decisions/`: stack.md, assumptions.md, baseline-amendments.md.
- `.flamin/sessions/`: handoffs and the step ledger. `.flamin/versions/`: v1.md, v2.md, ...
- `docs/`: DESIGN.md (how flamin works), README, WORKFLOW, VERIFICATION.

## Rules that are never broken

1. Only the Orchestrator talks to the human and delegates. Sub-agents never start other agents.
2. Before acting, the Orchestrator shows three lines: Understood, Planned (agent plus `flamin` command), Because (the rule or document).
3. Clarify as early as possible. Every assumption needs an explicit "agreed" from the human, recorded only by `flamin approve <id> --yes`. Silence is not agreement.
4. The flow is Step 0 intake, Phase 1 (Steps 1 to 5, ending with the baseline), Phase 2 (Steps 6 to 10 per module), Phase 3 (Steps 11 to 13). No step is skipped. `flamin status` shows where the product is.
5. Never edit a `FLAMIN:LOCK FULLY_LOCKED` file. In a `STRUCTURE_LOCKED` file, edit only between `FLAMIN:EXTENSION <name>` and `FLAMIN:EXTENSION:END`. Only `flamin generate` changes locked code.
6. Cross-module calls go only through the other module's `contracts` layer. Outer layers call inner layers, never the reverse.
7. In product work, never write to `.flamin/*.json`, `kit/`, the launchers, `.claude/`, `.codex/`, `.cursor/`, `CLAUDE.md`, `AGENTS.md`, `.git/` or `.flamin/stacks/`. For human-authorized kit bootstrap or maintenance, the main assistant may edit infrastructure sources directly; generated adapters and rule copies are updated only through the engine renderer. Engine state and `.git/` remain protected. This exception does not authorize bypassing hooks.
8. Approval Gates pause for the human: production release, app store submission, database migrations, deleting files, external changes (including GitHub API actions), paid API calls, secret or credential changes, baseline amend, new stack profile, new dependencies, CR list approval, stack choice, assumption agreement. Read-only external inspection, including GitHub releases, assets, branches and history, does not require approval solely because it accesses an external API; paid calls still require approval. Show the exact operation and affected repository, assets or refs where applicable. Approval covers only that operation, not unrelated actions. No answer means no. An agent never runs `flamin approve`.
9. When a flamin hook denies an action, report the complete reason and stop the denied action. Never bypass the hook. If approval is required, present the exact operation and use the engine's supported approval route. Approval handling must support both product and master-kit maintenance modes, including GitHub API actions. Read-only inspection must not require approval solely because it accesses an external API. If the supported route is missing, report the limitation and stop the denied action; do not create product state merely to approve kit maintenance.
10. Never put secrets in files or command lines.
11. Versions: v1 is built from scratch; every later change is a change request in the next version (v2, v3, ...).
12. When context nears 80%, write a handoff with three sections (Objective, State Ledger, Artifact References) and validate it with `flamin handoff --validate <file>`.
13. Saying "continue" means `flamin resume`: carry on from the next step, never redo finished work.
14. At the end of a session, update the `.flamin/` docs that remember what changed.
15. For master-kit maintenance commits, the human may explicitly waive the full test suite or all tests through the engine's supported approval route. The engine records the validation choice and reason, prepares the manifest from the staged contents, and verifies that the approved commit scope has not changed. Agents never approve their own waiver. A commit waiver does not authorize release, publication or unrelated operations.
