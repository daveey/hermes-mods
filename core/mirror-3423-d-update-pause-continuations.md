# Update pause: continuations and synthesized turns

Patches: **3132, 3140, 3164, 3165**.

## Behavior
- A synthesized turn (process completion, kanban notice) landing on a session paused for a core update now pauses at its next step boundary, so it no longer holds an unpublished admission reservation that blocks instant apply.
- A continuation with a pending pause yields before its first request/step boundary instead of ~minutes later.
- A paused turn's continuation refused during start-up releases its claim, logs the refusal, and re-arms the watcher (10 s backoff for a core-update pause, 30 s otherwise); an accepted prompt that never started logs a refusal with cause admission, closing or not_started.
- The resume note warns that earlier tool output may be stale and PR, CI or card state should be re-read.

## How it works
- 3132 gives a turn carrying a paused marker (`_carry_paused_marker`) the step-boundary hook, mid-tool cancel and clarify withdrawal.
- 3140 marks a continuation (only a literal `true`) so the loop may yield before the first request.
- 3164 handles the ignored False from `_run_prompt_submit` in the kickoff and adds refusal logging in `prompt_turn`.
- 3165 adds one sentence to the pause note.

## Code
- `tui_gateway/prompt_turn.py`, `tui_gateway/session_auto_continue.py`, `tui_gateway/update_pause.py`
- `agent/conversation_loop.py`, `agent/turn_iteration_prep.py`, `agent/update_pause_cancel.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_update_pause_superseded.py`: `test_synthesized_turn_over_a_paused_marker_pauses_at_its_next_step_boundary`, `test_synthesized_turn_over_a_paused_marker_keeps_running_without_a_pause_request`, `test_synthesized_turn_whose_carried_pause_was_retired_meanwhile_does_not_pause`, `test_synthesized_turn_over_a_paused_marker_arms_the_mid_tool_cancel_and_clarify_withdrawal`.
- `tests/agent/test_step_boundary_pause_first_request.py`: `test_continuation_with_a_pending_pause_yields_before_the_turn_starts`, `test_continuation_paused_during_setup_yields_before_its_first_request`, `test_continuation_without_a_pause_runs_as_before`, `test_ordinary_turn_still_waits_for_a_real_step_boundary`.
- `tests/tui_gateway/test_update_pause_first_request.py`: `test_only_a_literal_true_marks_a_continuation`, `test_a_continuation_turn_may_yield_before_its_first_request`, `test_any_other_turn_waits_for_a_real_step_boundary`.
- `tests/tui_gateway/test_update_pause_first_request_model.py`: `test_a_continuations_first_request_yields_and_the_turn_pauses`, `test_first_request_counts_as_in_flight_only_for_a_continuation`.
- `tests/tui_gateway/test_prompt_refused_logging.py`: `test_accepted_prompt_refused_at_admission_logs_its_refusal`, `test_prompt_refused_while_closing_names_closing`, `test_prompt_refused_for_a_replaced_session_names_not_started`, `test_accepted_prompt_that_runs_logs_exactly_one_finished`.
- `tests/tui_gateway/test_update_pause_refused_kickoff.py`: `test_continuation_refused_during_its_build_runs_once_after_the_hold_lifts`, `test_refused_kickoff_clears_its_claim_and_rearms_a_watcher`, `test_refused_crash_continuation_backs_off_instead_of_hanging`, `test_refusal_warning_names_who_retries`, `test_paused_continuation_refused_on_a_closing_record_does_not_rearm`, `test_submit_that_is_not_a_refusal_never_rearms`, `test_accepted_continuation_is_unchanged`, `test_watcher_whose_continuation_is_refused_hands_off_to_a_rearmed_watcher`.
- `tests/tui_gateway/test_update_pause.py`: `test_pause_note_warns_tool_output_may_be_stale`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Without these fixes a refused continuation could leave a chat paused until the backend exited (observed 40–51 minutes).
