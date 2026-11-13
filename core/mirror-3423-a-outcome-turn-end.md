# Outcomes: turn-end refresh keeps declared waits and newer turns

Patches: **2944, 2945, 2946**.

## Behavior
- A running outcome carries the stored wait declaration; a refresh during the live turn keeps it, and the turn's own turn-end refresh ignores its own unreleased lease/marker.
- A late refresh never discounts a newer turn's evidence: gateway refreshes keep a newer turn running, and a Desktop turn's marker is never named by a gateway refresh.
- The concluding identity is frozen before readmission, so settlement reuses an agent without retiring a successor's marker.

## How it works
- 2944 captures the concluding turn's live-turn evidence before scheduling the turn-end refresh.
- 2945 replaces the snapshot with `turn_identity(marker=, lease_holder=, gateway_token=)` built from handles only that turn holds.
- 2946 freezes the identity before readmission in `prompt_turn`, `turn_marker` and `session_auto_continue`.


## Code
- `agent/turn_facade_lease.py`
- `gateway/run_turn.py`
- `gateway/turn_outcome.py`
- `hermes_state_outcome.py`
- `tui_gateway/prompt_turn.py`
- `tui_gateway/session_auto_continue.py`
- `tui_gateway/turn_marker.py`
- `tui_gateway/turn_outcome.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_state/test_outcome_late_turn_end_refresh.py`: `test_a_failed_capture_discounts_nothing`, `test_a_gateway_refresh_still_discounts_its_own_unreleased_turn`, `test_a_late_gateway_refresh_keeps_the_newer_turn_running`, `test_capture_turn_identity_names_each_kind_of_evidence`, `test_own_turn_marker_reads_back_what_the_turn_recorded`, `test_the_gateway_names_its_agents_lease_and_its_events_token`, `test_the_gateway_never_names_a_concurrent_desktop_turns_marker`, `test_the_named_identity_is_exactly_what_the_store_reads_live`, `test_the_refused_turn_timer_discounts_the_refused_turns_own_evidence`, `test_the_refused_turn_timer_keeps_a_newer_turn_running`, `test_the_tui_turn_end_captures_its_own_evidence_when_none_is_passed`, `test_the_tui_turn_end_discounts_its_own_unreleased_evidence`, `test_the_tui_turn_end_refresh_after_settle_keeps_a_newer_turn_running`, `test_turn_identity_names_nothing_without_handles`.
- `tests/hermes_state/test_outcome_reused_agent.py`: `test_late_retirement_only_deletes_its_named_marker`, `test_settlement_reuses_agent_without_retiring_or_discarding_successor`.
- `tests/hermes_state/test_outcome_turn_end_declaration.py`: `test_a_newer_user_row_still_supersedes_a_carried_declaration`, `test_a_refresh_during_the_live_turn_keeps_the_declaration`, `test_both_turn_end_callers_pass_the_concluding_turn_never_a_blanket_bypass`, `test_the_turns_own_turn_end_refresh_ignores_its_unreleased_turn`, `test_without_a_declaration_running_still_carries_nothing`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
2945 removes `capture_turn_identity`; callers must use `turn_identity`.
