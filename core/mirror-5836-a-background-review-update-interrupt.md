# Background reviews yield to core updates (mirror 5455–5457)

## Behavior
- A background review no longer keeps its admission reservation past an update's drain claim hold. Its watcher interrupts the in-flight call once a pause request has stood `REVIEW_UPDATE_PAUSE_GRACE_S` (5 s); the review releases its reservation and is requeued. A running tool is not cut (the interrupt retries after it returns), a review stopped by a retirement wind-down keeps its own reason and is not interrupted, and a review whose last call finished or that already ended is not requeued.
- A review refused by the update's admission hold at spawn is queued (bounded by `_REVIEW_REQUEUE_MAX_ATTEMPTS`) rather than dropped, unless the backend is retiring, in which case it is still skipped.
- Requeued reviews are also recorded per session on disk (`review-carry` cache directory, hashed session key), so the session's own next finished, non-interrupted turn runs the review with the carried memory/skill flags; a review that starts clears the record. Records older than 7 days are dropped, `/refine` (focus/explicit) reviews are not carried, forks sharing the session id do not take the record, and only one taker can claim it.
- The watcher starts from the review thread itself via a low-level `_thread.start_new_thread`, exits with `end_background_review`, and never takes local-work custody (avoids hangs with faked threads).

## How it works
`hermes_cli/backend_retirement.py` provides `start_update_pause_watcher`; `agent/review_update_carry.py` persists carried reviews; `agent/review_idle_queue.py`, `agent/background_review.py`, `agent/turn_finalizer.py` and `run_agent.py` wire it up.

## Code
`agent/review_update_carry.py`, `agent/review_idle_queue.py`, `agent/background_review.py`, `agent/turn_finalizer.py`, `hermes_cli/backend_retirement.py`, `run_agent.py`.

## Tests
Source: `tests/hermes_cli/test_background_review_update_interrupt.py` (by test names: interrupt after the grace, no interrupt without a request or when it lapses within the grace, retirement reason kept, watcher exit, stop before the fork is built, ended review never relabelled, running tool not cut, end-to-end release/requeue/carry, carry record round trip/staleness/per-session/single taker, next finished turn runs it, spawn under the hold queued or dropped when out of attempts, retiring backend still skips; plus watcher exit and survival of a synchronous thread fake) and `test_background_review_update_pause.py`, which is touched without new test functions. Not executed in this mirror pass.

## Applying
Three patches in order; the last is a docstring fix.

## Gotchas
The interrupt cuts an in-flight provider call (or compression call) but not a running tool; carried records are best-effort and a lost record means one review fewer.

## plugin?
No.
