# Test pins for post-merge mutant survivors

Patches: **3660, 3674, 3687**.

## Behavior
Test-only changes (plus one docstring fix); production behavior is unchanged.
- Desktop: pins session-status-dot owner/route repaint rules, idle-motion-pause reload/resume handling, and kanban events-frame coalescing (dispose, first-frame re-read, one in-flight wait per key, interval restart, Blocked read not cancelled or duplicated).
- Gateway/state: pins that a shutdown Stop latched during a requeue write is consumed with the cancelled head and its retained media released; and that a locked NULL-active heal retries after the short backoff, keeps the delegate heal's failure, and re-warns after a success.
- Desktop build script: pins the `assert-dist-built` CLI hint on a timed-out chunk check (throttled/contended host, naming the `HERMES_CHUNK_CHECK_TIMEOUT_MS` knob) and that bundle-defect text is absent.

## How it works
- 3660 adds Desktop tests; `api.ts` only has its onEventsFrame docstring corrected to the real fallback polling intervals.
- 3674 adds a gateway test file and a hermes_state test file; one mutant is documented as equivalent and left untargeted.
- 3687 copies the script into a scratch desktop root and runs it as a CLI with a slow-node shim and 300 ms budget.

## Code
- `apps/desktop/src/plugins/kanban/api.ts` (comment only)
- `apps/desktop/scripts/assert-dist-built.mjs` (covered, unchanged)

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/electron/idle-motion-pause.test.ts`: re-applies after a reload whose stale-sheet removal throws; a window loading after resume stays unpaused.
- `apps/desktop/src/app/chat/session-status-dot.test.tsx`: relabel on tile gaining owner, owner tile rebound, route moving to a new profile, same-profile re-activation, running process named by an owner row.
- `apps/desktop/src/plugins/kanban/api.events-cursor.test.ts`: pending refresh dropped on dispose; first frame of new binding re-reads; one slow-read wait; interval restart on deferred re-read; Blocked read in flight.
- `tests/gateway/test_update_requeue_stop_latches.py`: `test_a_shutdown_stop_latched_during_the_requeue_write_is_consumed_with_the_head`, `test_the_stopped_heads_retained_media_is_released_when_it_is_cancelled`.
- `tests/hermes_state/test_schema_init_memo_heal_retry.py`: `test_a_locked_null_active_heal_is_retried_after_the_short_backoff`, `test_a_full_init_keeps_the_delegate_heal_failure_when_the_null_active_heal_succeeds`, `test_a_heal_that_succeeds_ends_the_failure_streak_so_the_next_failure_warns_again`.
- `tests-js/renderer-syntax-timeout.test.mjs`: the CLI tells a timed-out check apart from a bad bundle and names the budget knob (skipped on win32).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The 3687 test is skipped on win32.
- An update-claim discard in the requeue path is equivalent on all reachable paths, so it has no test.
