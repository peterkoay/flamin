## Codex action approval policy

Every Codex role, including the Orchestrator and sub-agents, may use recognized
read-only terminal commands. This Codex-specific permission overrides a role's
tool-neutral shell restriction only for read-only inspection.

Before changing or deleting files, present the complete proposed operation,
all affected files, the exact diff and the engine request ID to the Orchestrator
for human review. One multi-file edit is one approval request. Approval binds
the workspace, role and every file's before/after content. Changed content
requires a new approval. The human runs flamin approve <id> --yes in a terminal;
an agent never answers its own gate. No answer means no.

State-changing engine commands belong to the Orchestrator and require approval
for the exact command and working directory. Existing human-only gates remain
human-only. Scripts, compound commands, redirection and unknown invocations are
not read-only exemptions. Arbitrary shell mutations remain blocked because their
effects cannot be checked against ownership, locks and workflow rules. Use a
supported native edit or engine command instead; never evade a denial.

Approval never overrides ownership, locked code, engine state, credentials,
workflow order or destructive-action protections. Claude Code and Cursor retain
their existing policies.
