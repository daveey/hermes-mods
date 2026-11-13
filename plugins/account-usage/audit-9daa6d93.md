# Re-audit: core 9daa6d93b9 (core-update pause winds a background review down), main 7c16f596ed

Card t_01a1d55940415ace. Opened by account-usage-compat-watch: fork main moved `run_agent.py` off every audited
digest.

Drifted pins (old digests kept, new digest added):
- `execution-compatibility.json` `run_agent.py`: 2723695bbcbe (91c2b94744, last audited) -> dcaa59ee3812
  (9daa6d93b9). 9daa6d93b9 is the only commit touching `run_agent.py` since 91c2b94744.

The commit's other files (`agent/review_idle_queue.py`, `agent/turn_iteration_prep.py`,
`hermes_cli/admission_fence.py`, `hermes_cli/backend_retirement.py`, `cron/scheduler_tick.py`, tests) are not
pinned by account-usage. `check_compat.py --core <origin/main 7c16f596ed export>` reported only this pin.

What changed in `run_agent.py` (+8/-3), all inside `_spawn_background_review_now`:
- `retirement.begin_background_review()` now receives the agent's `session_id` (the reservation's owner record).
- On the review thread's exit, `review_idle_queue.requeue_update_paused_review` requeues a review that a
  core-update pause wound down (bounded by `_REVIEW_REQUEUE_MAX_ATTEMPTS`); otherwise the existing
  `_maybe_requeue_preempted_review` runs with the same kwargs dict as before.

Against selection.py's seams:
- Client factories, `recover_with_credential_pool`, `_failed_credential_identity`, `switch_model`,
  `interruptible_*_api_call`, `perform_api_call`: untouched. The change is background-review lifecycle only.
- A requeued review re-enters through the same idle-queue -> `_spawn_background_review_now` path the
  preempted-review requeue already used, with the same kwargs, so it builds its review agent exactly as before.
- No new credential or route fence: the new stop check (`turn_iteration_prep.begin_iteration` ->
  `backend_retirement.review_stop_reason`) reads admission/pause state only, and stops a review before its next
  provider call, never mid-call.
- selection.py unchanged, so recovery_wrapper and fast-rate-limit-fallback/account-usage-compatibility.json need
  no refresh.
