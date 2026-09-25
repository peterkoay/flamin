# flamin workflow

This is the day-to-day path through a product, with what you say, what the Orchestrator runs, and which gate stops the line. DESIGN.md §9 is the authority; this page is the walk-through.

On Windows use `.\flamin.cmd` in PowerShell or `./flamin.cmd` in Git Bash wherever this page says `flamin`.

## Every session

1. The Orchestrator runs `flamin status` first.
2. If it prints "flamin hooks are not running in this session", everything stops until `flamin doctor` is clean. Without hooks, the gates are only Soft.
3. It asks: "Continue product <name>, or start a new product?" A new product goes in a fresh copy of the master kit, never in this folder.
4. "continue" runs `flamin resume`: the last completed step, the next step, the latest handoff and the step ledger. Finished steps are never redone.

## Step 0: intake and stack choice

| You say | The engine records |
|---|---|
| "start a new project" | `flamin init` |
| answers to the Business agent's questions | `flamin intake --set name=... --set purpose=...` |

The 8 mandatory items halt the process until each is clear: product name, purpose, users and actors, core journeys, must-have features, target platforms, data sensitivity, success criteria. `flamin intake --check` lists what is missing.

Every question, answer and assumption goes into `.flamin/decisions/assumptions.md` as `## A-001: ...` with a `- Scope:` line. **Only you** make an assumption agreed, in a terminal: `flamin approve A-001 --yes`. Writing "agreed" in the notes changes nothing, and deleting a block does not close it.

After intake the Architect recommends the stack in plain words in `.flamin/decisions/stack.md` (saying up front if iOS needs macOS with Xcode). The Orchestrator opens the gate:

```
flamin intake --stack python-fastapi               # one profile
flamin intake --stack python-fastapi:backend --stack typescript-react:web   # several, each with its folder
flamin approve R-0001 --yes                         # you
```

On "yes" the engine writes `stack.json` and copies the profile into `.flamin/stacks/`. A new profile goes in `.flamin/decisions/proposed-stacks/<name>/` and through the same gate.

## Phase 1: system modeling (sequential)

| Step | Who | Engine command | Gate |
|---|---|---|---|
| 1 Requirements | Business, then Analyst writes `.flamin/analysis/<slug>/requirements.md` | `flamin analyze <slug>` | open assumptions refuse it |
| 2 Modules | Architect | `flamin model --add-module booking --depends catalog`, then `flamin model --step 2` | cycles refused |
| 3 Data model | Architect writes `.flamin/design/<module>/model.toml` | `flamin model --step 3` | a model per module |
| 4 Aggregates | Architect marks `aggregate_root = true` | `flamin model --step 4` | a root per module |
| 5 Baseline | Architect runs the checklist; you confirm | `flamin baseline-review`, then `flamin approve R-... --yes` | your "agreed" |

After the baseline, the data model is locked. A real gap is fixed with `flamin baseline-amend "<reason>" --modules a,b` (a gate; the reason and approver are logged first), and the window closes with `flamin model --done`.

## Phase 2: module development (parallel per module)

| Step | Who | Engine command | Needs |
|---|---|---|---|
| 6 Interface plan | Planner writes `.flamin/analysis/<module>/plan.md` (`## Batch 1`, 5 or fewer each) | `flamin plan <module>`, then your "yes" | baseline |
| 7 Design and generate | Designer writes one TOML spec per feature | `flamin lint <spec>`, `flamin design <module> <spec>`, `flamin generate <spec>` | Step 6 |
| 8 Business logic | Developer fills extension points | `flamin develop <module>` (lease), `flamin develop <module> --done` | every spec generated |
| 9 Wiring | Developer wires contracts; mocks if a module is not ready | `flamin wire <module> [--mock other] --done` | Step 8 |
| 10 Self-test | Tester writes and runs tests | `flamin module-done <module> --result pass` (also runs the lock and boundary scan) | Step 9 |

Rules the hooks enforce while agents work:

- `FULLY_LOCKED` files change only through `flamin generate`. In `STRUCTURE_LOCKED` files, only the body between `FLAMIN:EXTENSION <name>` and `FLAMIN:EXTENSION:END` may change.
- A module's code may import another module only through that module's `contracts` layer. Outer layers call inner layers.
- Product code needs a leased module, Step 7 done, and an open version with an approved CR list.
- Up to 10 modules can be leased at once, one agent per module.
- Regenerating keeps every extension body by name. A body whose name left the spec is moved to `.flamin/tmp/orphans/` and reported.

## Phase 3: integration and release (sequential)

| Step | Engine command | Needs |
|---|---|---|
| 11 Integration test | `flamin integration-test --result pass` (`--result fail --reopen a,b` sends modules back to Step 8) | every module at self-test pass |
| 12 Architecture review | `flamin architecture-review` (whole-product lock, boundary and cycle scan) | Step 11 pass |
| 13 Release | `flamin release`, then `flamin approve R-... --yes` or `--no` | Step 12 pass |

`flamin release` closes the version, raises the build number by exactly 1, and writes the release proposal (tests, open CRs, known risks, recommendation) into `.flamin/versions/v<n>.md`. Your yes or no is final for that version. A version that is not released stays in the history.

## Versions and change requests

- v1 is built from scratch through the full flow. Every later change is the next version: v2, v3, ...
- `flamin cr-new "<title>"` adds a CR to the open version. Once that version's CR list is approved, new CRs wait for the next version.
- Business and Analyst write each CR's impact, affected modules, risks and questions in `.flamin/versions/v<n>.md`.
- You approve the list: `flamin cr-approve v2 --cr CR-001=booking,catalog@7`, then `flamin approve R-... --yes`. The engine reopens only those modules, from that step. Nothing else is redone.
- A CR that changes the data model needs `flamin baseline-amend` first.
- Machine versions for tools and stores: v<n> is `<n>.0.0` plus the build number.

## Gates: how you answer

| Tool | What happens |
|---|---|
| Claude Code, interactive mode (default, acceptEdits, plan) | The hook answers "ask", and Claude Code's own prompt asks you. |
| Claude Code in bypass, dontAsk, auto or an unknown mode; Codex; Cursor | The hook denies and prints a request id. You run `flamin approve R-0012 --yes` in a terminal, then tell the agent to retry the exact same action. |

Gated: production release, app store submission, database migrations, deleting files, external or paid API calls, secret or credential changes, baseline amend, new stack profile, new dependencies, CR list approval, stack choice, assumption agreement. An approval covers only the exact command or diff shown.

## Handoff and memory

- When an agent's context nears 80%, the Orchestrator writes a handoff with three sections (Objective, State Ledger, Artifact References): `flamin handoff` makes a skeleton; `flamin handoff --stdin` writes and validates one; `flamin handoff --validate <file>` checks one.
- Every step command also appends to `.flamin/sessions/ledger.md`, so a lost context costs at most one step.
- At session end, if product code changed but nothing under `.flamin/` did, the hook shows a reminder to update the docs.

## Undo

Git is the undo button: end each step at a commit, and revert a bad step with git. The pre-commit hook runs `flamin check-staged` on every commit. For larger changes, open a new version with a CR.
