# Update pause: carried-turn stamp handling

Patches: **3375, 3411**.

## Behavior
- A carried notification turn that a backend exit cuts short (no user Stop before it) keeps the write-ahead `carried_turn_paused` stamp, so the next backend's continuation names the unfinished turn instead of cold-resuming without it.
- A user Stop before the exit still takes the stamp back, and a job stop that raises takes it back.
- A carried turn that completes normally just as the exit flag is set (the exit's interrupt did not land) takes the stamp back, so the continuation no longer names it.

## How it works
- 3375 adjusts `pause_stands` in `tui_gateway/update_pause.py` to keep the stamp for an exit-interrupted carried turn, and documents in `prompt_turn._record_turn_marker` why a turn that raised after its hook paid keeps the on-disk stamp.
- 3411 additionally requires the turn result to be `interrupted` before it counts as cut short by the exit, and has `prompt_turn` read the user-Stop flag through the shared `USER_STOP_AT_EXIT` constant instead of a literal string.

## Code
- `tui_gateway/update_pause.py`
- `tui_gateway/prompt_turn.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_update_pause_superseded.py`: `test_a_carried_pause_whose_job_stop_raises_does_not_name_the_completed_notification_turn`, `test_a_carried_turn_that_raises_after_its_hook_paid_keeps_naming_it`, `test_a_backend_exit_interrupting_a_carried_turn_after_its_hook_paid_keeps_naming_it`, `test_a_user_stop_before_a_backend_exit_of_a_carried_turn_still_takes_the_stamp_back`, `test_a_carried_turn_that_completes_normally_while_the_backend_exits_takes_the_stamp_back`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
