# Pool reset wait audit

Candidate core: `8fa054f240b9029bee39c7800ab3201e4eb4f59a` (fix(retry): wait for a pooled account's known reset).

Added digests:
- `agent/turn_recovery.py` `e30e45c9f2d15bcaa34aa8500c8592fc73474fe626f0930e8565cbcad3c0a38b`
- `agent/turn_api_error.py` `6dbd11af5ad2a41340d1e3f3e6f52671252d3c61d2a457a3c3217f1f863ecab5`

The commit adds `pool_reset_wait` (read-only: earliest reset of a walled `CredentialPool`) and uses it in
`compute_error_backoff` (wait until the reset plus 3 s when within 660 s) and `settle_unrecovered_error` (end retries
at once with the quota refusal when the reset is past the budget). Both only choose a sleep length or end the retry
cycle on a rate limit; neither rotates, selects or mutates a credential, and neither changes a client factory, route
or identity header. `recover_with_credential_pool`, `_failed_credential_identity`, `switch_model`, both
interruptible API-call seams and `perform_api_call` retain accepted digests. No request-local snapshot fence is
bypassed. No `selection.py` change or fast-rate-limit-fallback pin refresh is required.

All prior digests remain accepted for mixed-version rollout.
