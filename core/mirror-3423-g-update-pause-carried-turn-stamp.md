# Update-pause: carried pause stamp taken back; doc nits

Patches: **3314, 3347**.

## Behavior
- A carried (notification-turn) pause that does not stand, because the job stop failed or a user Stop won, no longer leaves a stamp that makes the continuation say "Finish what it still needs too".
- An earlier standing carried pause stays named when a later one does not stand.

## How it works
- 3314: the step-boundary hook takes back a stamp it added itself when it refuses the pause, records fresh stamps in `_carried_pause_stamped`, and `pause_stands` removes the stamp when the turn does not end paused.
- 3347 is comments/docstrings only.

## Code
- `agent/conversation_loop.py`
- `tui_gateway/prompt_turn.py`
- `tui_gateway/turn_marker.py`
- `tui_gateway/update_pause.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_update_pause_superseded.py`: `test_a_carried_pause_whose_job_stop_failed_does_not_name_the_completed_notification_turn`, `test_a_user_stop_of_a_carried_turn_after_its_hook_paid_does_not_name_the_stopped_turn`, `test_a_later_carried_pause_that_does_not_stand_keeps_an_earlier_carried_pause_named`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
