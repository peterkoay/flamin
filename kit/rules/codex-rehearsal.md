# FLAMIN Codex rehearsal

This copy supports chat continuation through Step 0 intake. Use the paired FLAMIN MCP server. The legacy Git backstop remains installed.

- On “continue,” call `flamin_continue` and use the saved step, eligible roles, missing inputs, handoff and pending decisions. Report a missing MCP tool as a setup blocker.
- Give Business the intake answers and ask it to record them in its owned Markdown using permitted native editing. Confirm the proposed state updates with `flamin_save_intake`. The confirmation form records intake only.
- Present formal decisions separately. `flamin_review_decision` shows the exact proposal. Use `flamin_confirm_decision` to present assumptions and stack decisions for human confirmation.
- Once intake is complete, ask the Architect to record a recommendation. Use `flamin_propose_stack` to open the decision and confirm it before starting Phase 1.
- Keep engine state, checksums, the kit, launchers and host configuration engine-owned. Follow host permissions. Stop and report a failed edit. If the owner cannot save, return a proposed diff and keep the work open.
