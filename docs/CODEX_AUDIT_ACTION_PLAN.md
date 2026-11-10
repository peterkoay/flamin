# flamin audit: proposed implementation plan

Date: 2026-10-10

Status: proposed changes only. This document does not authorize a commit, release, paid probe, or deletion. No implementation changes or new test runs were made as part of the review.

Scope: master-kit maintenance in this checkout. This plan is based on source inspection and Git metadata, and revises the recommendations in `docs/bolt_AUDIT_RECO.md`. The original audit's reported 147 engine-test passes and two release-test passes lack retained output and a commit identifier; they are not validation for the changes below.

## 1. Fix hostname lint false positives

Priority: first.

Evidence: `kit/engine/flaminlib/lint.py`, function `portability_problems`, collects user and machine names and matches them case-insensitively in kit and state files. A machine name of `localhost` therefore flags legitimate loopback examples and policy expressions.

Proposed change:

- Exclude exact generic loopback values `localhost`, `127.0.0.1`, `0.0.0.0`, and `::1` from the collected name set, using a case-insensitive comparison.
- Keep existing matching for other user and machine names.
- Do not strip `.local` or otherwise broaden hostname normalization.
- Add a focused regression test with mocked identity sources so the outcome does not depend on the test machine.

Acceptance criteria:

- Loopback identity values do not cause legitimate loopback text to be flagged.
- A distinctive machine or user name embedded in a scanned file is still flagged, including a case variant.
- Existing absolute-path detection remains effective.
- A later doctor run on the affected environment confirms whether the reported hostname failures are resolved. A clean overall doctor result must not be assumed from this fix alone.

## 2. Make release-builder Git failures understandable

Priority: second.

Evidence: `tools/build_release.py`, function `tracked_files`, runs `git ls-files --stage -z` with `check=True`. Its command failures currently propagate as an unhandled exception.

Proposed change:

- Handle expected Git invocation failures at an appropriate boundary and return a nonzero CLI exit code with a concise diagnostic.
- For a confirmed non-repository case, explain that the builder requires a cloned Git checkout with tracked kit files.
- Preserve the actual Git diagnostic for other failures; do not label permission errors or a damaged checkout as a missing repository.
- Handle a missing Git executable with a specific message.
- Keep manifest verification and other release integrity checks active.

Acceptance criteria:

- Non-repository, missing-executable, and other Git failures produce useful diagnostics without an unhandled traceback for those expected cases.
- Failure occurs before archive creation.
- Successful packaging retains its existing file selection, manifest checks, and archive modes.
- Focused tests cover the distinct failure cases, with existing release tests covering successful packaging and manifest mismatch rejection.

## 3. Add Python cache ignore rules

Priority: third.

Evidence: the root `.gitignore` lacks `__pycache__/` and `*.pyc`. Cache directories can consequently appear as untracked files and be accidentally staged.

Proposed change:

- Add `__pycache__/` and `*.pyc` to the root `.gitignore`.
- Add the same entries to `GITIGNORE` in `kit/engine/flaminlib/cmds.py`; `ensure_gitignore()` uses this list when initializing copies.
- Use supported rendering or initialization paths for generated copies; do not create an assumed second `.gitignore` file merely because the original audit mentioned one.

Acceptance criteria:

- Git ignore checks confirm that representative untracked cache directories and `.pyc` files are ignored.
- Newly initialized copies receive the intended rules through the engine.
- Verify initialization behavior in an isolated test fixture. Do not run `flamin init` in this master-kit checkout or turn maintenance mode off to perform this check.
- Check for already tracked caches separately: adding an ignore rule does not untrack existing files.

Deleting existing cache directories is unnecessary to obtain the ignore behavior. Leave deletion outside this implementation batch unless separately requested; project rules gate deletions.

## 4. Close the standalone bytecode packaging gap

Priority: optional hardening, suitable for the same batch if selected.

Evidence: manifest generation excludes `__pycache__` directories and `.pyc` files. The release builder excludes `__pycache__` path components, but does not generally exclude `.pyc` files. A tracked `docs/example.pyc` could enter an archive because `docs/` is included and is outside the kit manifest's coverage.

Proposed change:

- Exclude `.pyc` files explicitly from release file selection, alongside the existing cache-directory exclusions.
- Add a regression case for a tracked `.pyc` outside `__pycache__`, under an included directory such as `docs/`.

Acceptance criteria:

- The standalone bytecode file is absent from release file selection and generated archives.
- Normal documentation and source files remain included.
- Manifest mismatch checks still fail when an actual required kit file changes or disappears.

This is defense against accidentally tracked bytecode. Existing manifest exclusions do not guarantee that staging caches will be rejected by commit validation.

## Findings that require no implementation change

- Git tracks `flamin` as `100755` and `flamin.cmd` as `100644` in this checkout. The Windows batch launcher does not require a Unix executable bit.
- D-45 checks `flamin`, `kit/githooks/pre-commit.posix`, and `kit/githooks/pre-commit.windows`. All three are tracked as `100755` here.
- The archive builder already writes tracked modes into ZIP, tar, and npm archive metadata. This does not prove that every extraction tool restores those modes, but there is no demonstrated builder-mode defect from the reviewed evidence.
- This checkout already has Git history. Do not initialize it again or stage unrelated files with `git add -A`.
- `kit/ci/flamin-check.yml` is a sample workflow, not an active workflow at that path.

## Validation and completion record

The recommended core batch is items 1–3. Item 4 is a separate, small hardening choice. Before implementation, select the validation scope with the human as required by master-kit maintenance rules.

For targeted validation, use the focused lint and release-builder cases described above, plus ignore-rule checks. Record the exact commands, results, tested revision or working-tree scope, and any skipped checks. Do not report the previous audit's results as a new pass. If the human chooses targeted validation or no tests, do not run the full suite afterward.

Release tests require a separate run: they live in `tests/test_build_release.py`, while the engine's maintenance scoped-test runner executes from `kit/engine/tests`. Run the selected release checks directly, for example `python -B -m unittest discover -s tests -p test_build_release.py -v` from the repository root. This direct run does not create the engine's staged validation record. If a commit is requested, ensure the release results cover the files being staged and use the supported engine route for the required commit authorization; do not represent a direct release-test pass as engine-recorded scoped approval. If the selected validation cannot be represented by that route, report the limitation before committing.

If a commit is requested, follow the engine's supported maintenance validation and human-only authorization route. Any scoped or skipped-suite authorization must bind to the exact staged contents and HEAD. Refresh integrity metadata only through the supported engine path. Do not bypass hooks.

Live host-hook validation is separate from this code batch. Direct handler tests verify the handler behavior, not that Claude Code, Codex, or Cursor invokes it in a live session. A paid `flamin doctor --probe-codex` call needs its own authorization. No live probe is required merely to save or review this plan.
