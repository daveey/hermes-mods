# PR #1470 (wt/bench fp stale) compatibility audit

Candidate core: `f8fc63706116fff2bc0947e24b32bd60bab45af6` (1 commit on fork main `f05ad0de05`).

| File | Accepted (fork main) SHA-256 | Candidate SHA-256 |
|---|---|---|
| `agent/credential_pool.py` | `82a4c4e115f775c1cd9b382f358e6d476aeff494e16f4162e24f26bc436bab97` | `b6eed4a640c3655cfb5e07bca34cbdbd7eeb8ca99cf0b6203df57381c40e2460` |

Also changed by the PR, not pinned: `agent/credential_pool_pushed.py` (`_bench_predates_token`), tests.

## Findings

- `credential_pool._mark_exhausted` now also stamps `extra["bench_status_at"]` and reuses that same
  timestamp for `last_status_at`; a comment is reflowed. Status, cooldown, `failure_reason` and rotation
  semantics of `mark_exhausted_and_rotate` (the only pool write `selection.py` makes, keyed by
  `credential_id=snapshot.entry_id`, `api_key_hint=snapshot.token`) are unchanged. `selection.py` never
  reads `entry.extra`; eligibility (`_exhausted_until`, model cooldowns, `enabled_entries`) is untouched.
- `credential_pool_pushed._bench_predates_token` only trusts `bench_token_fp` when its stamp matches
  `last_status_at`. That narrows when an auth wall is called transient (kanban exit code); it adds no
  credential swap or route fence that a request-local snapshot could bypass. No plugin references
  `credential_pool_pushed`.
- None of the wrapped seams (client factories, `recover_with_credential_pool`,
  `_failed_credential_identity`, `switch_model`, `interruptible_*_api_call`, `perform_api_call`) change.

Verdict: compatible; `selection.py` unchanged, so recovery_wrapper and fast-rate-limit-fallback pins need
no refresh. Candidate digest added beside main's.

Verified: `plugins/account-usage/tests` 302 passed + 72 subtests under the installed runtime venv
against both installed core (main `201d4c6e1e`) and the candidate `f8fc637061`; `check_compat.py`
exit 0 against both.
