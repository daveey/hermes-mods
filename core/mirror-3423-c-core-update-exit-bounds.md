# Core update: pause long model requests and bound gateway exit

Patches: **3093, 3111**.

## Behavior
- A model request issued at a step boundary (after tool results) is cancelled after the mid-tool grace period when a core update pause is pending; the turn pauses resumably and releases its admission reservation. A turn's first request and a user Stop are untouched.
- After a core-update checkpoint is accepted, the gateway's post-stop tail is clamped to exit within the updater's drain promise; the control socket stop uses a bounded close wait; slow tails log phase marks.

## How it works
- 3093 registers boundary model requests with `update_pause_cancel` as `model-request` and re-issues the iteration through the redirect restart.
- 3111 adds `exit_deadline.set_exit_target`, set by `checkpoint_for_update` and cleared by `_cancel_restart`.

## Code
- `agent/turn_api_call.py`
- `agent/turn_api_error.py`
- `agent/update_pause_cancel.py`
- `gateway/exit_deadline.py`
- `gateway/control_socket.py`
- `gateway/run_bootstrap.py`
- `gateway/run_shutdown_restart_wait.py`
- `gateway/update_checkpoint.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_update_pause_model_request.py`: `test_a_long_model_request_at_a_step_boundary_yields_and_the_turn_pauses`, `test_the_first_model_request_is_not_a_boundary_and_is_left_alone`, `test_model_request_counts_as_in_flight_only_at_a_boundary`, `test_a_user_stop_is_not_a_model_yield`.
- `tests/gateway/test_update_exit_target.py`: `test_exit_target_clamps_waits_without_a_deadline`, `test_exit_target_and_deadline_take_the_earlier`, `test_tail_exits_by_the_update_exit_target`, `test_tail_bounds_the_control_socket_stop`, `test_control_socket_stop_does_not_wait_on_a_held_connection`.
- `tests/gateway/test_update_checkpoint.py`: `test_cancelled_restart_clears_the_update_exit_target`, `test_accepted_checkpoint_sets_the_exit_target_inside_its_drain_timeout`, `test_refused_or_unaccepted_checkpoint_sets_no_exit_target`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The exit target is soft: it clamps waits but never force-exits the process.
