# Update pause publication respects a late user Stop

Mirror entries: [2263](patches/2263-daveey-Decide-update-pause-at-terminal-publication-after-late-Stop.patch), [2264](patches/2264-daveey-Serialize-pause-publication-separately-from-session-history.patch).

## Behavior

- Decide whether an update pause still stands at terminal message publication, after payload construction can yield to a user Stop.
- Serialize Stop revocation with pause announcement, message.complete and watcher startup without recursively taking the session history lock.

## How it works

prompt_turn moves pause_stands evaluation to the publication boundary. update_pause.publication_lock creates a separate per-session threading.Lock under history_lock; pause revocation and terminal publication share that lock. This replaces the first follow-up's attempt to publish while holding the non-reentrant history lock.

## Code

- `tui_gateway/prompt_turn.py`
- `tui_gateway/update_pause.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/tui_gateway/test_update_pause_user_stop.py` — `test_stop_cannot_revoke_pause_in_the_middle_of_publication`, `test_a_stop_never_drops_another_turns_pause`.

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

This documents the late-Stop publication race, not a redesign of update continuation or a live restart test. Lock ordering matters: do not replace the dedicated publication lock with nested history_lock acquisition.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
