## What this project is

This folder uses **flamin**: a team of AI agents builds software through a fixed flow, and a small engine keeps the flow honest. The engine owns all state in `.flamin/` and all checks. Run it as `./flamin <verb>` (Linux, macOS), `.\flamin.cmd <verb>` (Windows PowerShell) or `./flamin.cmd <verb>` (Windows Git Bash).

**Master kit.** If `flamin status` says "Master-kit maintenance mode is ON", this is kit maintenance, not a product: the product flow does not apply, kit files may be changed, and every kit change must pass `flamin kit-maintenance test` before it is committed. Only a human turns the mode on or off (D-46).

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
7. Never write to `.flamin/*.json`, `kit/`, the launchers, `.claude/`, `.codex/`, `.cursor/`, `CLAUDE.md`, `AGENTS.md`, `.git/` or `.flamin/stacks/`.
8. Approval Gates pause for the human: production release, app store submission, database migrations, deleting files, external or paid API calls, secret or credential changes, baseline amend, new stack profile, new dependencies, CR list approval, stack choice, assumption agreement. No answer means no. An agent never runs `flamin approve`.
9. When a flamin hook denies an action, report the reason in full and stop. Never look for another route around it.
10. Never put secrets in files or command lines.
11. Versions: v1 is built from scratch; every later change is a change request in the next version (v2, v3, ...).
12. When context nears 80%, write a handoff with three sections (Objective, State Ledger, Artifact References) and validate it with `flamin handoff --validate <file>`.
13. Saying "continue" means `flamin resume`: carry on from the next step, never redo finished work.
14. At the end of a session, update the `.flamin/` docs that remember what changed.
