# Update-pause continuations hand off to the gateway owner

Patches: **3495–3497**.

## Behavior
- A turn paused for a core update in a chat whose turns are owned by the gateway's durable ledger no longer loops on refused slot claims. The paused continuation is queued in the owner's FIFO and the pause marker is retired by the forwarding backend.
- Work no running gateway will finish (unknown admission, no owner, refused or unconfirmed hand-off) retires the pause with a WARNING naming the reason and the request.
- A newer pause of the same turn is never retired by an older hand-off.
- Other slot refusals keep the existing backoff.
- "Window ended without an update" is logged once per pause across re-armed and carried-turn watchers.
- The continuation note is reworded once later turns have run: it names the pause time and age, says later turns ran since, asks the reader to check before redoing anything, and never says the previous turn was interrupted. A continuation that paused again names its chain (the request already ran as a continuation after N earlier pauses).
- The paused turn's own request row is not counted as a later turn.

## How it works
- 3495 forwards a ledger-refused paused continuation through `owner_forward.forward_prompt`, compares on `paused_at` under the marker lock before retiring, and makes `pause_note` count the transcript's user rows after `paused_at` (stale wording also when queued behind the owner's turns, or when the count is unreadable and the pause is over 10 minutes old). The new note prefix is classified auto_continue by the session-history legacy sniff and the gateway replay stripper.
- 3496 extends the stale/queued note to cover a continuation that itself paused again (marker prompt is the root request, pause count above 1).
- 3497 excludes the marker's own prompt from `later_turns_since`, so a request row stamped after the pause stamp does not count as a turn that ran over it.

## Code
- `gateway/run.py`
- `tui_gateway/update_pause.py`
- `tui_gateway/session_auto_continue.py`
- `tui_gateway/session_history.py`
- `tui_gateway/turn_marker.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_update_pause_ledger_owner.py`: `test_gateway_owned_pause_goes_to_the_owner_once_and_is_retired_here`, `test_forwarded_continuation_is_never_worded_as_the_previous_turn`, `test_owner_that_will_not_run_it_retires_the_pause_with_a_reason`, `test_other_owner_refusals_keep_the_existing_backoff`, `test_handoff_does_not_retire_a_newer_pause`, `test_window_end_is_announced_once_per_pause_across_rearmed_watchers`, `test_continuation_after_later_turns_names_the_pause_and_the_turns_since`, `test_continuation_right_after_the_pause_keeps_the_previous_turn_note`, `test_old_pause_whose_later_turns_cannot_be_counted_is_worded_as_stale`, `test_continuation_that_paused_again_is_forwarded_once_and_named_as_a_chain`, `test_the_paused_turns_own_request_row_is_never_a_later_turn`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The session-history and replay-stripper classification of the new note prefix has no dedicated test in these patches (untested).
- Stale wording depends on counting stored user rows; when the count is unreadable only pauses older than 10 minutes get it.
