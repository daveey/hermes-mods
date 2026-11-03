# Compatibility audit: core PR 801

Candidate: `27576ed88c6fd8e36c2d14d13868740b3e80bcd5` (upstream sync, merge of
NousResearch/hermes-agent `fd267bb324`). Base: fork main
`0f4792607ebeaa5c1a015f349b75357d194d7e67`, whose digests for the four files
below were already pinned (last entry of each list).

## Drifted files (main -> candidate)

- `agent/agent_runtime_helpers.py`: the `_recover_auth_failure` body moves to the
  new `agent/credential_auth_recovery.recover_auth_failure` (same keywords, passed
  as `**kwargs`); `_MAX_AUTH_REFRESH_ATTEMPTS` moves with it. On a Nous 401 it now
  allows one forced refresh per episode, then backs off (5/15/30 s) and swaps to
  the store's current row instead of re-POSTing. `_auth_pool_refresh_counts`
  values become per-episode dicts instead of ints.
- `agent/credential_pool.py`: `_refresh_entry`, `_refresh_entry_impl` and
  `_try_refresh_current_unlocked` take an optional `failed_key`; the Nous branch
  compares a peer rotation against the bearer the request sent.
  `try_refresh_matching` passes `api_key_hint` through. `mark_exhausted_and_rotate`
  and every name `selection.py` imports (`_exhausted_until`,
  `_parse_absolute_timestamp`, `_decode_jwt_claims`, `PooledCredential`,
  `get_env_prefer_dotenv`, `FAILURE_REASON_BILLING_UNVERIFIED`) are unchanged.
- `agent/turn_recovery.py`: the singleton Nous 401 refresh is skipped once the
  pool spent this turn's forced refresh (`nous_pool_refreshed_this_turn`).
- `agent/anthropic_adapter.py`: `claude-mythos` joins the mandatory-thinking
  families (a request-shape constant only).

## Seam review

- Client factories, `recover_with_credential_pool`, `_failed_credential_identity`,
  `switch_model`, both `interruptible_*_api_call` functions and `perform_api_call`
  are byte-identical. None gains a decorator.
- With a manual-choice snapshot active, `recovery_wrapper` never calls the
  original `recover_with_credential_pool`, so the new backoff path (and its live
  `agent._swap_credential`) is never reached; it marks the snapshot's entry and
  picks the next request-local snapshot as before.
- `nous_pool_refreshed_this_turn` only reads `_auth_pool_refresh_counts`, which a
  snapshot attempt never writes; the turn_recovery change can only skip a
  refresh, never add a credential swap. No new credential or route fence that a
  request-local snapshot could bypass.
- `selection.py` needs no change, so fast-rate-limit-fallback's
  `account-usage-compatibility.json` stays as it is.

Append the four candidate digests. Keep every existing digest: hosts run main
and the candidate side by side during rollout.
