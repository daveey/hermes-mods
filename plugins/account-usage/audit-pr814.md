# Compatibility audit: core PR 814

Candidate: `b530dd381892` (fix(credential-pool): persist probe-restored Codex quota
across reloads). Base: fork main `b1f608189c`, whose `agent/credential_pool.py`
digest (`ebe46dd9a5...`) is already pinned.

## Drifted file (main -> candidate)

- `agent/credential_pool.py`:
  - `_normalize_error_context` moves verbatim to `agent/credential_pool_timestamps.py`
    and is re-imported; the `reset_delay_from_message` import moves with it.
  - The quota-probe restore path keeps the superseded failure's `last_status_at`
    on the ok row instead of `None`, so the auth-store recency merge
    (`hermes_cli/auth_pool_merge.py`, not pinned) no longer lets a stale on-disk
    cooldown win the flush.
  - Every name `selection.py` / the dashboard imports (`_exhausted_until`,
    `_parse_absolute_timestamp`, `_decode_jwt_claims`, `PooledCredential`,
    `get_env_prefer_dotenv`, `FAILURE_REASON_BILLING_UNVERIFIED`, `STATUS_DEAD`,
    `load_pool`) is unchanged.

## Seam review

- Client factories, `recover_with_credential_pool`, `_failed_credential_identity`,
  `switch_model`, both `interruptible_*_api_call` functions and `perform_api_call`
  live in other files and are untouched by this PR.
- `selection.py` never reads `last_status_at`; `_exhausted_until` only applies to
  exhausted rows, so an ok row's new stamp does not change snapshot eligibility.
  The probe-restore only clears an exhaustion core already proved stale; it adds
  no credential or route fence a request-local snapshot could bypass.
- `selection.py` needs no change, so fast-rate-limit-fallback's
  `account-usage-compatibility.json` stays as it is.

Append the candidate digest; keep every existing digest (hosts run main and the
candidate side by side during rollout).
