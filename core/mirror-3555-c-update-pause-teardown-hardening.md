# Update-pause wake re-queue and managed-worker teardown hardening

Patches: **3548, 3549**.

## Behavior
- A notifier wake whose scope cannot be update-paused (for example a detached-route Kanban wake) no longer starts a turn inside the update hold; it is re-queued until the hold lifts.
- A steering prompt that arrived while the wake waited on the turn lease is re-queued with the wake instead of the wake being lost; the steered prompt runs once as its own turn, then the wake runs after the hold lifts.
- If a steering transfer was already delivered to the turn, the re-queue is still refused, now with the detail "steering transfer delivered".
- A broken pipe while tearing down a managed worker (the child already exited) no longer replaces a completed reply with a failure; teardown errors are logged instead.

## How it works
3548 makes `installed()` set only the turn-start gate for a held-class claim whose scope is ineligible, and remove it after the run. `requeue_unstarted` reads the owner's `steer_transfers` after execution returned, withdraws undelivered tickets, and passes them to the store, which requeues each transfer the turn never took in the same write (FIFO order preserved). Without `transfers` the old refusal ("steering transfer exists") stands.

3549 makes the managed worker's `close()` suppress a BrokenPipeError from re-flushing a control frame (like `Popen.communicate`) and runs every owner teardown through `close_quietly`, which logs `OSError` rather than letting it set the turn outcome, including on the restart path.

## Code
- `gateway/session_update_pause.py`
- `gateway/session_update_pause_hook.py`
- `gateway/session_update_pause_ingress.py`
- `hermes_state_update_pause.py`
- `gateway/session_kanban_restart.py`
- `gateway/session_managed_worker.py`
- `tests/gateway/test_update_hold_claimed_wake.py`
- `tests/hermes_state/test_requeue_unstarted_input.py`
- `tests/gateway/test_managed_worker_drain.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_update_hold_claimed_wake.py`: `test_wake_claimed_before_the_hold_goes_back_to_its_fifo_until_the_hold_lifts`, `test_detached_route_wake_gets_only_the_turn_start_gate`, `test_a_steer_committed_while_the_wake_waited_on_the_lease_goes_back_with_it`, `test_real_turn_runner_and_agent_requeue_the_unstarted_wake_then_run_it_once`.
- `tests/hermes_state/test_requeue_unstarted_input.py`: `test_an_undelivered_steer_transfer_goes_back_with_the_wake_in_the_same_write`, `test_a_steer_transfer_the_turn_took_refuses_the_requeue`, `test_a_steer_transfer_with_no_transfer_read_still_refuses`.
- `tests/gateway/test_managed_worker_drain.py`: `test_close_tolerates_a_control_frame_that_raced_the_childs_exit`, `test_finished_turn_keeps_its_answer_when_worker_teardown_raises`, `test_close_finishes_when_closing_stdout_or_stderr_fails`, `test_restart_loss_is_booked_when_worker_teardown_raises`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A managed-worker session never binds a pause scope, so it stays outside the turn-start gate (documented on `claim_hold_gate`).
- Completion-note rows (`noted_completion`) are not treated as notifier wakes.
