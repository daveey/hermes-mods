# A running background review holds backend retirement

Patches: [`patches/0700-fix-retirement-a-running-background-review-holds-the.patch`](patches/0700-fix-retirement-a-running-background-review-holds-the.patch) (fork commit `4101ef7442`),
[`patches/0703-fix-update-stop-a-stale-dashboard-backend-through-it.patch`](patches/0703-fix-update-stop-a-stale-dashboard-backend-through-it.patch) (fork commit `2d9f16aa2c`, the `hermes update` stop path),
[`patches/0706-fix-retirement-expose-background-reviews-to-cross-pr.patch`](patches/0706-fix-retirement-expose-background-reviews-to-cross-pr.patch) (fork commit `a75e611b06`, the cross-process admission reservation)

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
- `hermes update`'s dashboard step (`_kill_stale_dashboard_processes(restart_managed=True)`, patch 0703) no longer
  sends a plain SIGTERM first. For each stale backend whose port (spawn ledger, host rendezvous record, or a
  non-zero `--port`) and session token (`HERMES_DASHBOARD_SESSION_TOKEN` in its environment, or the rendezvous
  token) it can find, it calls `POST /api/health/retirement` `prepare`. While `GET /api/health/idle` reports
  `background_review:<n>`, it asks again every 2 s, so the fence lets the review finish, winds it down between
  calls past 300 s, and drops it past 900 s (also the update's own deadline). On a permit it commits (admission
  closed, no new review starts) and then stops the backend as before. Any other answer (idle, a different busy
  ledger, an old backend with no fence, no token or port) is stopped at once, as before. Update output:
  `⏳ PID <n> is running a background review (background_review:1); waiting for it before stopping …`, then
  `✓ PID <n> finished its background review after <s>s; retirement fence committed, stopping it` (or
  `⚠ … stopping it anyway` at the deadline). `hermes dashboard --stop` keeps its immediate stop.
- `begin_background_review` also takes an installation-root cross-process admission reservation
  (`admission_fence.reserve` on `get_default_hermes_root`, not the profile home) before the review is admitted, and
  keeps it until `end_background_review`. A review with no turn lease still counts as one reservation, so an update
  hold that already reads reservations sees it. Patch 0700 left the drain probe unchanged on purpose; this patch
  does not add a probe either. The review now holds the reservation that reader already counts.
- A new review is refused while an update hold stands (`reserve` returns None because the hold is set). Another
  review's reservation does not by itself refuse a new one; each review takes its own. `OSError` or `TimeoutError`
  from `reserve` also admits nothing, and the process count stays 0.
- If retirement is already preparing when the reservation lands, the reservation is closed and the review is not
  admitted. If the review thread fails to start, both the process hold and the reservation are released.
  `end_background_review` closes the reservation outside the process lock; close is idempotent.
- A live drain-window apply, and a bounded window expiring instead of cutting a long call, are untested in this
  patch. Those windows already wait on admission reservations and expire on their own bound; this patch does not
  change that.

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

`tests/hermes_cli/test_update_stale_dashboard_review.py` (patch 0703; 3 of 5 fail without it, and the 2 controls
pass both ways). The backend is a real child process: a real `RetirementFence` behind an HTTP listener that checks
the session token from its environment, plus a SIGTERM handler that records what the shutdown would do to a review
still running:

- A review in the middle of a provider call: the update stops the backend only after the review completes.
  Without the patch, the stop records `review_cancelled_mid_call` 0.2 s after it starts.
- A review past the wait bound winds down at a call boundary, and then the backend is stopped.
- A wedged review: the update gives up at its deadline and stops the backend as before.
- Controls: a backend with no review, and one with no reachable fence, are stopped at once as before.

`tests/hermes_cli/test_background_review_update_admission.py` (patch 0706; 6 tests, posix):

- A review with no turn lease holds one installation-root reservation until end. `set_hold` sees `reservations == 1`
  while `active_count() == 0`. After that hold is set, a second `begin_background_review` returns None and the
  reservation count stays 1. Ending the review twice leaves zero reservations, and a successor can begin after the
  hold is cleared.
- A review started with `HERMES_HOME` under `profiles/writer` reserves the installation root, not the profile directory.
- `reserve` raising `OSError` or `TimeoutError` admits nothing and leaves the process count at 0.
- If retirement starts preparing after `reserve` succeeds, the reservation is released and begin returns None.
- A review thread that fails to start releases both the process hold and the reservation, and clears the agent's run token.

## How it works

`RetirementFence.begin_background_review` calls `admission_fence.reserve` before taking the process lock. The
reservation is stored on `BackgroundReviewHold` and closed in `end_background_review` after the process set drops
the hold. `reserve` returns None while an update hold stands, which refuses the review. Another review's reservation
does not. The drain probe is the existing admission reader; it is not a new poll.

## Code

`hermes_cli/backend_retirement.py` (`begin_background_review`, `end_background_review`, `BackgroundReviewHold.reservation`).

## Applying

`git am -3 core/patches/0706-fix-retirement-expose-background-reviews-to-cross-pr.patch` after 0700. It touches
`hermes_cli/backend_retirement.py`, which 0700 and 0703 also touch.

## Gotchas

The reservation is on the installation root even when `HERMES_HOME` is a profile directory. A lock error fails
closed: the review does not start. A long call keeps the reservation; this patch does not cut it off, and it does
not prove that a drain window expires instead.

## Plugin?

No: it sits inside core retirement and admission.
