# FLAMIN workflow in Codex

Use the paired FLAMIN MCP server for engine operations. The Orchestrator calls `flamin_continue` to find the saved step, eligible roles, handoff and pending decisions. Ask each role to prepare its owned artifacts, then record the completed work with the tool for that step. Use these tools wherever a role prompt refers to a legacy FLAMIN command. Report a missing tool as a setup blocker.

| Step | What the Orchestrator calls |
| --- | --- |
| 0 Intake and stack | `flamin_save_intake` for the Business answers. Once the Architect records a recommendation, use `flamin_propose_stack`. |
| 1 Requirements | `flamin_record_analysis` after the Analyst saves requirements. |
| 2–4 Model | `flamin_add_module` for module division. Use `flamin_complete_model_step` after each modeling step is ready. |
| 5 Baseline | `flamin_propose_baseline` after reviewing modules and models. Use `flamin_propose_baseline_amendment` and `flamin_close_baseline_amendment` when a confirmed baseline needs changes. |
| 6 Plan | `flamin_propose_plan` after the Planner saves the interface batches. |
| 7 Design | `flamin_record_design` to lint and record the spec, then `flamin_generate`. Pass the filename stem for the spec. |
| 8–9 Development and wiring | `flamin_lease_module`, `flamin_complete_development` and `flamin_record_wiring`. Use `flamin_release_module_lease` to give up a lease. |
| 10–11 Tests | Run the product tests and record the observed results with `flamin_record_module_test` and `flamin_record_integration_test`. |
| 12–13 Review and release | `flamin_review_architecture` runs the engine checks. `flamin_propose_release` opens the local release decision. |
| Follow-up changes | `flamin_raise_change_request` and `flamin_propose_change_list`. |

Use `flamin_review_decision` to see the proposal and `flamin_confirm_decision` to present it to the human. A confirmed local release decision records the release in FLAMIN. Publishing is a separate action.

Use `flamin_verify_snapshot` to check a staged tree or candidate commit against a reviewed base commit. Keep engine state, checksums, the kit, launchers and host configuration engine-owned. Follow host permissions. If an edit fails, report the path and proposed change.
