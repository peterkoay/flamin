# flamin Audit: Recommended Changes (2026-10-10, revised)

Scope: master kit. The engine was exercised for all three tools — Codex, Claude Code, and
Cursor — plus doctor, lint, engine tests, release tests, and hook probes.

Important scoping note: this audit ran in a sandboxed copy that is NOT a git checkout and whose
files are periodically reset to mode 644. Several observations below are artifacts of that
environment and are marked as such; they do not apply to a normal cloned checkout (git history
present, launcher modes tracked correctly).

## Findings and recommended changes

### 1. Hostname lint false positive (the one real, code-backed defect)

`portability_problems` in `kit/engine/flaminlib/lint.py` (line ~158) collects user and machine
names from `getpass.getuser()`, `USERNAME`/`USER`, `platform.node()` and `COMPUTERNAME`, then
matches them case-insensitively as whole words in kit and state files. There is no exclusion of
generic loopback hostnames, so on any machine whose hostname is `localhost` (common in
containers and CI sandboxes), the checker flags:

- `kit/engine/flaminlib/policy.py` lines 143-144 — "localhost" appears only inside regex
  exclusions that *allow* loopback URLs in the external-API gate.
- `kit/engine/tests/test_checks.py` line 218 — a test fixture command (`curl
  http://localhost:8000/x`).

Recommendation: add a narrow exclusion for generic loopback names (`localhost`, `127.0.0.1`,
`0.0.0.0`, `::1`) to the collected name set. Do not additionally strip `.local` suffixes or
"normalize" hostnames: that widens matching and creates new false positives. After the change,
re-run `flamin doctor` on the affected machine to confirm the failures are gone — do not assume
a clean result.

### 2. Release builder error handling (valid improvement)

`tools/build_release.py` calls `git ls-files --stage` with `check=True` (line ~39). In a
directory that is not a git repository, or a broken checkout, this raises a raw
`CalledProcessError` traceback. Note: this is an edge-case robustness improvement, not a defect
in a normal checkout (where the repo exists and the command succeeds).

Recommendation: catch the failure in `tracked_files()` (or `main()`) and exit with a clear
message such as "not a git repository; run tools/build_release.py from a cloned kit checkout".

### 3. Python cache ignores (housekeeping only)

Stray `__pycache__/` directories exist under `kit/engine/flaminlib/`, `kit/engine/tests/`,
`tests/` and `tools/`. The stated packaging risk in the first draft was wrong:

- `kit/engine/flaminlib/manifest.py` (lines 10-17) already excludes `__pycache__` parts and
  `.pyc` files when building `kit/MANIFEST`.
- `tools/build_release.py` (line 27) already excludes `__pycache__` (and other cache parts)
  from release archives.

So caches can never leak into the MANIFEST or the release archives as-is. What remains is
plain housekeeping: the root `.gitignore` has no `__pycache__/` / `*.pyc` entries, so an
inattentive `git add -A` could still stage them (they would then be rejected by the manifest
verification, fail-closed, but noisily).

Recommendation: add `__pycache__/` and `*.pyc` to `.gitignore` and delete the existing stray
directories.

### 4. Environment artifacts (no action needed in a normal checkout)

These were observed in the audit sandbox only and should NOT be treated as kit defects:

- **Launcher modes.** The sandbox resets `flamin` to mode 644, causing "Permission denied".
  In a real checkout, git already tracks `flamin` as 100755 and `flamin.cmd` as 100644, which
  is correct: `flamin.cmd` is a Windows batch launcher and needs no Unix executable bit.
  D-45 (`kit/engine/flaminlib/cmds.py` line ~950) checks exactly `flamin` and the two git hook
  scripts — not `flamin.cmd`. The release builder preserves tracked git modes in all three
  archives (zip external_attr, tar mode fields), so no release-pipeline change is needed.
- **No git repository.** The sandbox has no `.git`; a real checkout does. Consequences
  observed in the sandbox (`check-staged` refusal, D-45 skip) do not apply. Do not run
  `git init` / `git add -A` in the real checkout — there are untracked files to consider.

### 5. Codex CLI availability

Codex was not installed in the audit environment, so the deep Codex checks (config-key
acceptance via `--strict-config`, live hook probe) were skipped by design. The rendered Codex
adapter itself is correct and all three hook entry points exit 0 when invoked directly.

Recommendation: on a machine with Codex installed, run `flamin doctor --probe-codex` (one
model call) to prove the hooks fire inside Codex itself. Remember Codex silently skips changed
hooks until they are re-trusted in `/hooks`.

## Verification evidence and its limits

- 147 engine tests and 2 release tests passed in the audit environment. Outputs were not
  retained and results are not pinned to a commit identifier; re-run against the target commit
  before relying on them.
- Adapter file comparisons (Claude, Cursor, Codex) and direct hook probes (deny contracts,
  allow paths, subagent gate) were run by invoking the hook handler directly with crafted
  payloads. This proves the engine's side of the contract only — it does NOT prove that each
  host tool (Claude Code, Codex, Cursor) actually registers and invokes the hooks in a live
  session. For Claude Code, doctor's in-process probe covers the rendered settings; for Codex
  and Cursor, host-level confirmation requires the respective live probes / trust flow on a
  machine with those tools installed.
- `kit/ci/flamin-check.yml` is a sample workflow meant to be copied to
  `.github/workflows/flamin-check.yml` in a product repo; it is not an active workflow at its
  kit location.

## Verified as correct in the kit (no change needed)

- Claude Code: `.claude/settings.json`, `CLAUDE.md`, and 8 agent files match the rendered kit;
  PreToolUse deny contract (JSON + exit 2) and allow path verified directly.
- Cursor: 7 agent files match; hook contract (JSON deny, exit 0) verified for `preToolUse`;
  `failClosed: true` on the four deny-relevant hook events; subagent launch gate denies
  unknown agent names; no orchestrator agent shipped (correct by design).
- Codex: `.codex/config.toml` valid, `[agents] max_depth = 1` and concurrency 10 present, all
  7 agent files match, hook commands wired for all 4 events.
- Release packaging preserves git file modes; manifest generation and release packaging both
  exclude Python caches.

## Priority order

1. Hostname lint fix (#1) — narrow loopback exclusion, then verify `flamin doctor` on the
   affected machine.
2. Friendly release-builder error outside git (#2).
3. Cache ignores + delete stray `__pycache__` (#3).