# Pre-audit: core PR #1827 (wt/update pause model yield, head 024bc37b2594)

Card t_01a1d2acd3f22d9d. Merge base = fork main 3e6f01049555. One commit (t_01a1d293248f5e12), 4 files:
`agent/turn_api_call.py`, `agent/turn_api_error.py`, `agent/update_pause_cancel.py`, one new test.

Drifted pins (main digest kept, PR digest added):
- `execution-compatibility.json` `agent/turn_api_call.py`: bd629a747d91 (main) -> 701f3c3f7942 (PR)
- `execution-compatibility.json` `agent/turn_api_error.py`: afd21a40ab9a (main) -> c4a469a27d17 (PR)
- `fast-rate-limit-fallback/compatibility.json` `agent.turn_api_error:handle_api_error`: 0cb8a48ee093 (main) -> 814aa0a92333 (PR)
  (AST digest computed with the managed Python 3.14.9.)

What changed:
- `perform_api_call` gains an optional `messages=None` kwarg and wraps the existing
  `run_llm_execution_middleware(...)` call in `update_pause_cancel.model_request_in_flight(...)`, which only
  registers a "model-request" in-flight id with the core-update pause watcher (no credential/route state).
  After the call, if the update's cancel is the pending interrupt, the response is dropped and the iteration is
  restarted via `_retry.restart_with_redirected_messages = True` (new helper `yield_to_update_pause`).
- `handle_api_interrupt` and `handle_api_error`'s existing `_interrupt_requested` branch gain the same yield
  before the user-redirect/abort path. In `handle_api_error` it sits after `recover_after_classification`
  (unchanged order) and before `route_classified_error`, exactly where the existing interrupt return/break is.

Against selection.py's seams:
- `perform_api_call` is the frame `calling_agent()` walks to; the middleware still runs inside that frame (a
  `with` body), so `f_locals['agent']` is found as before. `verified()` re-compiles the pinned file, so the
  new digest is the only change needed.
- Client factories, `recover_with_credential_pool`, `_failed_credential_identity`, `switch_model`,
  `interruptible_api_call` / `interruptible_streaming_api_call`: untouched.
- The yield restart re-issues the request through the middleware, so selection runs again per request; no
  request-local snapshot crosses it, and no new credential/route fence is introduced.
- fast-rate-limit-fallback: the rate-limit path (recovery -> route_classified_error) is unchanged; the new
  branch only fires when an update-pause interrupt is already pending, where the old code also left the loop.

selection.py unchanged, so recovery_wrapper and fast-rate-limit-fallback/account-usage-compatibility.json need
no refresh.
