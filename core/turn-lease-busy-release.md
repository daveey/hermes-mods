# Turn lease: a release that hit a busy state.db does not block the next turn

Patch: 0736 (fork `1611048a7c`).

## Behavior

`DurableTurnLease.release` used to log one ERROR when its DELETE lost the state.db write lock, and the finished turn's row stayed until its 300 s TTL. A same-process holder is never "dead", and Desktop/TUI admit with `wait_seconds=0`, so that chat's next message was refused as "another active writer".

Now:

- `release_session_turn_lease` marks the holder abandoned (per process, keyed by db path + holder) when the DELETE fails with a lock error. `try_acquire_session_turn_lease` treats an abandoned holder's row as reclaimable.
- A live, never-abandoned same-process lease is still refused.
- `DurableTurnLease.release` logs WARNING `lease release deferred` and retries the DELETE on a daemon thread with backoff until the lease would expire. One ERROR `lease release retries exhausted` is logged only if every attempt fails (coach detector signal).

## How it works

The abandoned-holder set is process-local, so it only unblocks the process that lost the lock; another process still sees a normal row until the retry deletes it or the TTL expires. The retry thread is a daemon, so it never delays shutdown.

## Code

- `agent/turn_facade_lease.py`: `DurableTurnLease.release`, background retry with backoff.
- `hermes_state_compression.py`: `release_session_turn_lease` (abandon mark), `try_acquire_session_turn_lease` (reclaim).

## Tests

`tests/hermes_state/test_session_turn_lease.py`, four new cases (the shipping commit recorded three red on the previous main and green after; the refused-live-lease guard passes on both):

- `test_locked_release_frees_the_lease_for_the_next_same_process_turn`
- `test_live_same_process_lease_is_still_refused`
- `test_facade_release_retries_a_locked_delete_in_the_background`
- `test_facade_release_logs_error_when_retries_run_out`

Re-run for this mirror on fork `origin/main` `a65917ae05`: the file passes (27 tests, 0 failed). The pre-fix red run was not repeated here, so the three-red claim is the shipping commit's record. Untested: a second process observing the abandoned row (cross-process behavior).

## Applying

Apply after 0735 with `git am -3`. Numbering follows fork order after the already-published 0729/0730.

## Gotchas

- Abandonment is per process and in memory; it is lost on restart (the row then expires by TTL).
- The ERROR line is the only signal the coach detector keys on. The new WARNING is expected under contention and is not a failure.
- Core change, not a plugin hook.
