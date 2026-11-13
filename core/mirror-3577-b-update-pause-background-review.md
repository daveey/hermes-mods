# Core-update pause winds down a background review

Patches: **3567**.

## Behavior
- When a core-update drain asks turns to pause, a running background review stops at its next step boundary (before its next provider call, never mid-call) and releases its admission reservation, so the drain's worker reclaim is no longer blocked by it.
- A review wound down this way is requeued on the idle queue; after the maximum requeue attempts, or if the queue refuses it, it is dropped with a log line. The queue is in-memory, so a backend restarted by the update loses it.
- The idle queue dispatches nothing while the update's admission hold is set.
- Admission reservations now record an owner (purpose, pid, session, creation time); `admission_fence state-details` lists them as `reservation_records` with age. An empty or unreadable record reads as an unnamed holder.
- A review that starts while the hold is set is still refused; a retirement wind-down keeps its own exit reason.

## How it works
3567 adds `review_stop_reason(hold)` in `backend_retirement`, called at every review step boundary in `turn_iteration_prep.begin_iteration` (also after a preflight compression). A live pause request (request and hold set, before expiry) sets the review's wind_down with reason `update_pause`. `run_agent._spawn_background_review_now` requeues such a review via `review_idle_queue.requeue_update_paused_review`. `Reservation.describe(purpose, session)` writes a JSON owner record into the reservation file; reviews, turn admissions and cron ticks label theirs. The lock remains the claim, so the count protocol and reservation ids are unchanged.

## Code
- `agent/review_idle_queue.py`
- `agent/turn_iteration_prep.py`
- `cron/scheduler_tick.py`
- `hermes_cli/admission_fence.py`
- `hermes_cli/backend_retirement.py`
- `run_agent.py`
- `tests/hermes_cli/test_background_review_update_pause.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_background_review_update_pause.py`: `test_review_stops_before_its_next_provider_call_once_the_drain_asks_turns_to_pause`, `test_a_request_without_the_hold_or_past_its_expiry_does_not_stop_the_review`, `test_retirement_wind_down_keeps_its_own_exit_reason`, `test_a_review_that_starts_while_the_hold_is_set_is_still_refused`, `test_gateway_review_winds_down_releases_its_reservation_and_is_requeued`, `test_requeued_review_waits_for_the_update_hold_then_dispatches`, `test_review_out_of_requeues_is_dropped_with_a_log_line`, `test_state_details_names_the_reviews_reservation`, `test_state_details_reports_an_unlabelled_reservation_without_failing`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The requeue queue is in-memory; a review is best-effort and may be lost across a backend restart.
- Cron-tick and turn-admission labelling is not directly covered by a listed test (untested).
