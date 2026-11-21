# TUI: refresh the stored outcome when an in-memory session is reaped

Mirror entries 5137, 5138.

## Behavior

- Automatic in-memory session ends (orphan reap, disconnect, idle expiry, LRU eviction) now refresh the stored session outcome, as the database orphan sweep already did. Before, a chat whose later reply was fine could keep an `unresolved{turn_failed}` outcome until a manual `sessions outcome --refresh`.
- At turn end the still-held active lease is discounted when the last-turn state has not yet been copied.
- The refresh uses the server-bound session database.
- 5138: the automatic refresh is skipped while a refused, update-pause or carried continuation marker still holds recovery custody; their writer remains the live backend after teardown and an unqualified refresh would change an interrupted outcome to running. An unreachable active-holder fallback was removed.

## How it works

- `tui_gateway/turn_outcome.py` holds the refresh and the marker check; `tui_gateway/session_lifecycle.py` calls it from the teardown paths.
- Interrupted automation deliveries must not paint a waiting chat.

## Code

- `tui_gateway/turn_outcome.py`
- `tui_gateway/session_lifecycle.py`

## Tests

Source tests (not executed in this mirror pass):

- `tests/tui_gateway/test_turn_outcome_refresh.py`: `test_a_later_good_reply_clears_stored_turn_failed_without_manual_refresh`, `test_closed_session_refresh_clears_stored_turn_failed_after_a_later_good_reply`, `test_ws_orphan_teardown_refreshes_closed_session_outcome`, `test_refresh_uses_server_bound_session_db`, `test_reap_preserves_outcome_with_held_continuation_marker`, `test_refused_turn_then_reap_does_not_store_running`.
- `tests/hermes_state/test_session_outcome.py`: `test_turn_end_clears_stored_turn_failed_after_a_later_good_reply`, `test_interrupted_automation_delivery_does_not_paint_a_waiting_chat`.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Refresh is deliberately skipped for held continuation markers; do not "fix" that by refreshing unconditionally.

## Why core instead of a plugin

Session lifecycle teardown and the stored-outcome derivation are core TUI-gateway code; a plugin has no teardown hook that runs before the live session state is dropped.
