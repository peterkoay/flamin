# Codex action approval maintenance — 2026-11-10

The human authorized implementation and explicitly skipped the maintenance
suite. Regression coverage is updated but is not recorded as executed.

Recognized literal terminal reads are available to all Codex roles. Native
multi-file edits are validated first, then gated as one exact operation.
Approvals include workspace, role and before/after content. Supported engine
mutations require the exact command and working directory, with the Orchestrator
as caller. Arbitrary scripts, compound commands, redirection and unchecked shell
mutations remain blocked. Existing protections and human-only gates remain.

Codex guidance comes from kit/agents/codex-policy.md and the renderer. Generated
adapters must not be edited by hand. Claude Code/Cursor retain their policies.

## Human-run deployment

After reviewing and staging only this change in the master kit, if committing:

    .\flamin.cmd kit-maintenance authorize-commit --validation skipped --reason "Codex policy change; maintenance suite explicitly skipped by human" --yes

This prepares the manifest and binds authorization to staged contents and HEAD.
Changed staging invalidates authorization; it does not approve an upgrade.

In a human terminal in C:\Users\WJKWEEJI\Code\Playground\TestPeteDemo, preview:

    .\flamin.cmd upgrade --from "C:\Users\WJKWEEJI\Code\TocoAI\flamin_v3_Claude"

Review every changed/removed path: this covers the whole kit, not just policy.
Only after approving the complete preview, apply:

    .\flamin.cmd upgrade --from "C:\Users\WJKWEEJI\Code\TocoAI\flamin_v3_Claude" --yes

The upgrade process has already imported the old renderer before copying the
new engine. After the human-approved upgrade, run the new renderer separately:

    .\flamin.cmd doctor --fix

This can also archive inactive adapters and an obsolete Orchestrator sub-agent
adapter; review those effects before running it. The hook matcher changes.
Refresh Codex trust using /hooks, then start fresh agents. Run flamin doctor
and flamin status in the product afterward.

## Validation and upgrade review

M-0004 was approved by the human and its exact corrective patch was applied.
The supported check-syntax command passed for all 37 Python files.
The diff whitespace check passed. Git also reported its LF/CRLF conversion
notices and inability to read the user's global ignore file.
The maintenance suite is SKIPPED; behavioral tests were not run.
The live edit gate blocked the corrective patch before approval and accepted
it after approval. This observation does not establish full runtime coverage.

A read-only byte comparison using the upgrade source-file selection found
168 replacement candidates, no removals: 151 differ only by CRLF/LF, and
17 have content differences. This was an independent inspection, not execution
of the human-only upgrade command.

Six content-different kit files belong to this policy change:
kit/agents/codex-policy.md; kit/engine/flaminlib/cli.py, hooks.py, policy.py,
render.py; and kit/engine/tests/test_round5.py.

The other 11 content differences must also be reviewed before upgrading:
kit/agents/business.md and orchestrator.md;
kit/engine/flaminlib/cmds.py, commit_validation.py, flow.py and maintenance.py;
kit/engine/tests/test_assumption_tool_routing.py, test_commit_validation.py
and test_maintenance_approvals.py; kit/MANIFEST; and kit/rules/core.md.
Some of the six overlapping files may also carry older differences.
The manifest has not been refreshed for this uncommitted change; use the
supported human maintenance route before distributing the kit.

TestPeteDemo was not upgraded or edited. Pre-existing untracked files in the
master kit were preserved. No scratch files were created or removed.
No commit, formal validation waiver, adapter regeneration or hook-trust change
was performed. Product intake remains separate.
