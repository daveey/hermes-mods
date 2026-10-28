# A running background review holds backend retirement

Patch: [`patches/0700-fix-retirement-a-running-background-review-holds-the.patch`](patches/0700-fix-retirement-a-running-background-review-holds-the.patch) (fork commit `4101ef7442`)

## Why

The post-turn memory/skill review fork runs on a daemon thread with no session turn lease and no admission
reservation. The retirement fence's idle proof (`busy_ledger`) read a backend mid-review as idle. On 2026-10-28
04:38 PDT the code-skew retirement of an SSH-isolated backend committed while two reviews were mid-call. The
shutdown cancelled both (`reason=session_ended phase=model_wait`), and one of them was in the middle of a skill
change, which was lost.

## Behavior

- `_spawn_background_review_now` takes `retirement.begin_background_review()` before it starts the review thread
  and releases the hold when the thread ends. After a retirement permit has been issued, a new review is skipped
  (logged `Background review skipped: this backend is retiring`), so nothing starts that the shutdown would cut off.
- `RetirementFence.prepare()` refuses while a review holds the fence. A skew or idle retirement therefore waits for
  the review to finish.
- If a review has run for `BACKGROUND_REVIEW_WAIT_S` (300 s) when a retirement is pending, `prepare()` sets the
  hold's `wind_down`. The fork then stops before its next provider call (`begin_iteration` exit
  `review_retirement_wind_down`), never in the middle of one.
- Once a review is older than `BACKGROUND_REVIEW_HARD_CAP_S` (900 s), it stops counting, so a wedged review cannot
  starve retirement.
- `busy_ledger()` reports `background_review:<n>` for the idle-exit watchdog and `/api/health/idle`.
- Unchanged: the hermes-sync drain-window probe. The tree move did not kill the reviews; the backend retirement
  2 minutes later did. Gating the drain on reviews would bring back update starvation.

## Tests

`tests/hermes_cli/test_background_review_retirement.py` (7 of 8 fail without the change):

- A running review with no leases blocks `prepare()` until it ends, and is not wound down inside the bound.
- Past the wait bound, `wind_down` is set. Past the hard cap, retirement proceeds.
- No review starts while a permit is held or after commit. It starts again after cancel.
- The real skew watchdog does not retire while a review runs and does not wind it down. It retires once the review ends.
- `busy_ledger()` names the review and drops it past the hard cap.
- `begin_iteration` breaks before the next provider call on `wind_down`.
- `_spawn_background_review_now` holds the fence for the thread's whole run, and skips cleanly (run token released)
  on a retiring backend.
