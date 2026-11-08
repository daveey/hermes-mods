# Execution compatibility audit: 176cb9d46ae4

Candidate: `176cb9d46ae4d3b7b11c11091ff12ee532cd446b`.
Baseline: `d7d9f2088d25c7486ca8526d2aca2afc55659122`.
Each of the seven baseline file SHA-256 values matches an existing accepted pin.
All existing digests are retained for mixed-version rollout.

## Seam assessment

- `agent/client_lifecycle.py`: credential adoption/refresh and route-client rebuilds now reset route verdicts when the endpoint changes, and failed adoption restores prior verdicts. Request-local Anthropic/Codex factories retain their signatures and identity-header checks. Selection requires the same provider/model/endpoint and never adopts another endpoint; it must not reset verdicts for a same-route account choice.
- `agent/chat_completion_helpers.py`: removes obsolete request-builder options and clarifies route-scoped streaming/reasoning verdicts. Interruptible dispatch and request-client ownership are unchanged; terminal wrappers still call the real core implementation and enforce route/model/header identity.
- `agent/agent_runtime_helpers.py`: primary transport recovery and model switching reset the expanded route-verdict set. Selection's switch wrapper delegates to core, then rejects pending choices if the route changed. `recover_with_credential_pool` and `_failed_credential_identity` retain their contracts.
- `agent/credential_pool.py`: `peek(model=...)` can now filter a current credential by model availability. Selection does not use `peek`; its authoritative hydrated entries are checked for model cooldown/entitlement, and recovery still passes the model to `mark_exhausted_and_rotate`.
- `agent/turn_recovery.py`: reasoning repair is now once per route visit, not once per turn/session; Codex soft-failure classification moves to `turn_recovery_codex`. Selection neither snapshots nor overwrites these retry fields. Its recovery wrapper only handles rate-limit, billing, auth and model-entitlement failures; reasoning recovery remains in core.
- `agent/turn_api_error.py`: exhaustion compares the reasoning retry's route visit. No changed selection call signature or credential attribution.
- `agent/turn_response_check.py`: imports the moved Codex soft-failure classifier; failed/cancelled responses retain their classified pool-recovery path.

Also read the candidate `route_verdicts` and `turn_recovery_codex` implementations. Route visit counters are compatibility-repair bookkeeping, not a new credential authorization fence. Existing managed-routing refusal remains in force; snapshot dispatch cannot override class routing or the current-OAuth-entry requirement.

No `selection.py` change is needed; its peer compatibility digest remains unchanged.
The combined fallback test must seed `_disable_streaming_seed=True` as well as its current value: candidate core correctly restores this configured seed on fallback, so changing only the old route verdict no longer selects the fixture's nonstreaming mocked wire seam. The pre-fix candidate run reproduced four fallback subtest failures; after fixing the fixture, both full account-usage runs pass.

## Verification

Using the installed dependency environment's Python 3.14.9:

- `scripts/test-all -k account-usage --hermes-core <installed-core>`: account-usage and repo-guards PASS.
- `scripts/test-all -k account-usage --hermes-core <candidate-worktree>`: account-usage and repo-guards PASS.
- `python3 plugins/account-usage/check_compat.py`: exit 0 against installed core.
- Same compatibility check with `--core <candidate-worktree>`: exit 0.

These are local suite/compatibility receipts, not evidence of already-running remote process activation.
