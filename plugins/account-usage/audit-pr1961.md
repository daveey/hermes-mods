# Pre-audit: core PR #1961 (wt/update pause yield nits, head 008d651da761)

Card t_01a1d3d430469bae. Fork main at audit time: da52aff8f4c0.

Drifted pins (main digest kept, PR digest added):
- `execution-compatibility.json` `agent/turn_api_error.py`: c4a469a27d17 (main) -> 7c89717db5b9 (PR)
- `fast-rate-limit-fallback/compatibility.json` `agent.turn_api_error:handle_api_error`: main digest already
  listed -> 84c407bc4d03 (PR)

No other pinned core file differs between main and the PR head (the PR touches only `agent/turn_api_error.py`
plus tests).

What changed in `handle_api_error` (+8/-5): the core-update pause yield
(`update_pause_cancel.model_yield_requested(agent)` -> `yield_to_update_pause(_retry)`, verdict `"break"`) moved
from inside the late `if agent._interrupt_requested:` block to right after the spinner stop, before
`recover_before_classification`.

Against selection.py's seams:
- `model_yield_requested` -> `_yield_requested` already requires `agent._interrupt_requested` and the update-pause
  issuer, so the branch fires for exactly the same requests as before; only its position moved.
- For those requests the error path now skips the request-error hook, offline wait, credential-pool recovery /
  rotation, retry count and error-attempt log. So `recover_with_credential_pool` and
  `_failed_credential_identity` (selection.py's wrapped seams) are no longer reached for an update-pause cancel:
  a request-local account snapshot can no longer record a spurious failure or rotate away from the chosen
  account because of a pause. That is strictly fewer calls into the wrapped seams. No new credential or route
  fence is added for a snapshot to bypass.
- Client factories, `switch_model`, `interruptible_api_call` / `interruptible_streaming_api_call`,
  `perform_api_call`: untouched.
- fast-rate-limit-fallback: a pause cancel was never a rate limit; skipping its recovery path for that case
  leaves the rate-limit fallback flow unchanged for real provider errors.
- selection.py unchanged, so recovery_wrapper and fast-rate-limit-fallback/account-usage-compatibility.json need
  no refresh.
