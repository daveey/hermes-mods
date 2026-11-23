# HTTP profile-scan admission test synchronization

Source patch ordinals: 5552.

## Behavior

The profile-scan responsiveness test waits for transient shared-pool cache peeks to finish before asserting zero shared tokens, while the expensive sessions scan remains blocked. No production request behavior changes in this patch.

## How it works

The test retains the real synchronous `/api/status` request and response bound, then waits for `limiter.borrowed_tokens == 0`. A controlled schedule holds a real `/api/sessions` cache peek through status JSON validation and releases it on the next event-loop turn. This distinguishes short cache work from an expensive scan wrongly admitted to the shared pool.

## Code

- `tests/hermes_cli/test_profile_scan_http_admission.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`test_profile_scan_burst_keeps_status_responsive` retains status response validation, single sessions-lane admission and pool separation. `test_session_peek_can_outlive_status_response` forces the ordering in which status completes before another request's peek.

## Applying

Apply to the HTTP admission regression suite with its async waiting helper and existing sessions-lane implementation. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

A successful status response is not a join barrier for concurrent requests. Fixed loop-yield counts do not prove pool quiescence. This test does not identify historical pool borrowers.

## plugin?

No. This is core HTTP regression synchronization.
