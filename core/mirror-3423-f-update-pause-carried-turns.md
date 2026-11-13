# Core-update pause: carried turns and first-request follow-ups

Patches: **3240–3241, 3280**.

## Behavior
- A synthesized notification turn that ran over a paused marker and is itself paused for the next update window is announced as paused (Desktop path untested; tests cover the gateway hook), and its continuation note tells the model that a notification turn was paused too and its partial work is above.
- A turn that retries its own pause after a failed job stop is not treated as carried and does not mention a notification turn that never ran.
- A user stop of a carried turn after its hook paused keeps the paused request.
- 3280 changes no behavior: it documents first-request pause tracking, the `pause_before_turn` early return, and the three-window `MAX_PAUSES` park bound.

## How it works
- 3240 stamps the marker `carried_turn_paused` when the pause paid is a carried one, appends `CARRIED_TURN_NOTE` to the continuation, and keys publication on `update_pause.effective_pause_key` (own marker key, else the carried paused key). A fresh `record_turn_start` marker clears the flag.
- 3241 decides "carried" from `not st.marker_key` instead of the marker reason; the reason only decides whether the pause is counted again.
- 3280 adds comments and tightens a first-request test to assert a literal False.

## Code
- `tui_gateway/update_pause.py`
- `tui_gateway/prompt_turn.py`
- `tui_gateway/turn_marker.py`
- `tui_gateway/session_auto_continue.py`
- `agent/conversation_loop.py`
- `agent/update_pause_cancel.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_update_pause_superseded.py`: `test_a_carried_turn_paused_for_the_update_is_announced`, `test_a_user_stop_of_a_carried_turn_after_its_hook_paused_keeps_the_paused_request`, `test_the_continuation_of_a_pause_a_carried_turn_paid_names_the_interrupted_notification_turn`, `test_a_pause_the_paused_turn_paid_itself_does_not_name_a_notification_turn`, `test_a_turn_retrying_its_own_pause_after_a_failed_job_stop_is_not_carried`.
- `tests/tui_gateway/test_update_pause_first_request.py`: assertions tightened by 3280 (no new test names).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- After three pause windows are released without an update applying, a continuation parks (bounded by `PARK_MAX_S`) without a step; this is intentional so a window that cannot apply does not rerun the same request.
