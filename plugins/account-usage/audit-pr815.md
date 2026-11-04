# Compatibility audit: core PR 815

Candidate: `207e594b603f` (Upstream sync 207e594b60). Its branch forked from main
before `83614574cf` (PR 814, probe-restored Codex quota), so the PR head's own
digests are all already pinned (`credential_pool.py` = `0c118953fc...`, the PR 801
audit). The new digest comes from the merge result: `git merge-tree --write-tree
origin/main 207e594b60` gives `agent/credential_pool.py` = `fb2ee21c2a72...`.
The other three files that differ from main (`agent_runtime_helpers.py`,
`anthropic_adapter.py`, `turn_recovery.py`) merge to digests already pinned
(`dd843a0d0f`, `d40c526006`, `599abfeea2`).

## Drifted file (main `83614574cf` -> merged candidate)

- `agent/credential_pool.py`: the same Nous transient-401 change audited for PR 801
  (`6afe040f7e`), now on top of PR 814's probe-restore:
  - `try_refresh_matching(api_key_hint=...)` passes the hint through
    `_try_refresh_current_unlocked` / `_refresh_entry` / `_refresh_entry_impl`
    as `failed_key`.
  - The Nous refresh compares a peer-rotated store key against the bearer the
    request sent (`failed_key`) instead of the row's possibly older copy, so it no
    longer adopts the key that just got the 401.
  - Public signatures only gain optional keyword arguments; every name
    `selection.py` / the dashboard imports (`_exhausted_until`,
    `_parse_absolute_timestamp`, `_decode_jwt_claims`, `PooledCredential`,
    `get_env_prefer_dotenv`, `FAILURE_REASON_BILLING_UNVERIFIED`, `STATUS_DEAD`,
    `load_pool`) and `mark_exhausted_and_rotate` are unchanged.

## Seam review

- Client factories, `recover_with_credential_pool`, `_failed_credential_identity`,
  `switch_model`, both `interruptible_*_api_call` functions and `perform_api_call`
  are untouched in this merge.
- `selection.py` rotates a manual snapshot with `mark_exhausted_and_rotate`
  (unchanged) and never calls the refresh path. The change is internal to the
  pool's Nous refresh and adds no credential or route fence a request-local
  snapshot could bypass.
- `selection.py` needs no change, so fast-rate-limit-fallback's
  `account-usage-compatibility.json` stays as it is.

Append the merged digest; keep every existing digest (hosts run main and the
candidate side by side during rollout).
