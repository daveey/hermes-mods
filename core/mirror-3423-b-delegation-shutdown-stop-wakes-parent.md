# Background delegation stopped by gateway shutdown wakes its parent

Patches: **3030, 3031, 3040**.

## Behavior
- A background subagent stopped by gateway shutdown is stamped `interrupted_by=<reason>` (persisted, so post-restart replay has it) and treated as failed, so an unviewed parent runs a turn saying the shutdown, not the user, stopped it; unattended wakes stop at a limit until the user returns.
- A user-requested stop (`/stop`, chat close, `/new`, CLI exit) stays unstamped and the parent stays asleep; a requested stop followed by shutdown keeps its first intent, and a shutdown stop stays stamped when a requested stop follows.
- The "not woken" warning is logged only for results kept as notes.

## How it works
- 3030 adds `by_shutdown` to `async_delegation.interrupt_all`, passes it from `gateway/run_shutdown`, and makes `delegation_wakes_parent` count the stamped child as failed.
- 3031 adds tests for warning exclusions and closed-chat logging; production code unchanged.
- 3040 adds `_mark_stop` to record the first stop's intent and validates requested-stop admission metadata.

## Code
- `tools/async_delegation.py`
- `gateway/run_shutdown.py`
- `gateway/run_notifications.py`
- `gateway/session_completion_notes.py`
- `gateway/session_automation.py`
- `tools/process_registry_notifications.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_shutdown_kill_restart_source.py`: `test_shutdown_sweep_marks_delegation_stops_as_shutdown`.
- `tests/gateway/test_unviewed_delegation_failure_turn.py`: `test_unit_a_gateway_shutdown_stopped_wakes_the_parent`, `test_unattended_wakes_stop_at_the_limit_until_the_user_returns`, `test_failure_in_a_chat_its_viewer_closed_stays_a_note`, `test_requested_stop_is_logged_only_when_it_is_kept_as_a_note`, `test_unviewed_successful_result_is_a_note_without_a_warning`.
- `tests/tools/test_delegate_shutdown_stop_wakes_parent.py`: `test_shutdown_stop_of_a_batch_is_stamped_and_wakes_the_parent`, `test_shutdown_stop_of_a_single_unit_is_stamped_and_wakes_the_parent`, `test_requested_stop_stays_asleep`, `test_session_end_stop_stays_asleep`, `test_shutdown_stamp_without_stopped_children_has_no_stop_guidance`, `test_requested_stop_warning_only_for_final_delegations`, `test_requested_stop_is_flagged_only_for_final_delegations`, `test_requested_stop_admission_metadata_is_validated`, `test_shutdown_before_finalize_keeps_the_first_stops_intent`, `test_shutdown_stop_stays_stamped_when_a_requested_stop_follows`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
