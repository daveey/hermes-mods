# Update-pause auto-continue: refused kickoffs, stale refusals, yield before error accounting

Patches: **3183, 3224, 3225**.

## Behavior
- A refused auto-continue kickoff leaves no marker inputs behind, and a persistently refused paused continuation stops re-arming at a cap. A continuation refused for ownership does not re-arm; one refused by the real admission hold releases and re-arms.
- A stale turn-owner refusal from an earlier submit no longer blocks the re-arm (3224).
- During an update pause, a cancel that surfaces as a provider error yields without error accounting, and a response that crosses the cancel is dropped and the turn pauses (3225).

## How it works
- 3183 and 3224 change `tui_gateway/session_auto_continue.py`: cleanup of marker inputs when the kickoff never started, a re-arm counter with a cap, and clearing the stale refusal before the submit.
- 3225 moves the pause yield ahead of API-error accounting in `agent/turn_api_error.py`.

## Code
- `tui_gateway/session_auto_continue.py`
- `agent/turn_api_error.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_update_pause_refused_kickoff.py` and `tests/tui_gateway/test_update_pause_refused_kickoff_admission.py`: `test_kickoff_that_never_started_leaves_no_marker_inputs`, `test_persistently_refused_paused_continuation_stops_rearming_at_the_cap`, `test_paused_continuation_refused_for_ownership_does_not_rearm`, `test_continuation_refused_by_the_real_admission_hold_releases_and_rearms`, `test_stale_owner_refusal_from_an_earlier_submit_does_not_block_the_rearm`.
- `tests/tui_gateway/test_update_pause_model_request.py`: `test_a_cancel_surfacing_as_a_provider_error_yields_without_error_accounting`, `test_a_response_that_crosses_the_cancel_is_dropped_and_the_turn_pauses`.
- `apps/desktop/src/app/session/hooks/use-message-stream/paused-for-update-stream.test.tsx` gained a streamed-bubble pin in 3225 (test name not extracted).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
