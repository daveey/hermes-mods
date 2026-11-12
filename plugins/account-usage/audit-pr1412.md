# PR #1412 (wt/pool auth wall transient) compatibility audit

Candidate core: `cbc6f2e1fedc892c996c52bd49d8fe84ae755a02` (1 commit on fork main `3b88d6ddb7`).

| File | Accepted (fork main) SHA-256 | Candidate SHA-256 |
|---|---|---|
| `agent/credential_pool.py` | `bbc6ef330a04a154f98ef0d6f128430ebe756fb945f56764db9d1ee18cce35eb` | `82a4c4e115f775c1cd9b382f358e6d476aeff494e16f4162e24f26bc436bab97` |
| `agent/turn_recovery.py` | `322e19cc4a6109055985cbbebff56bec961f861c300075b06e1fb03c5d495476` | `bbb2cb2e991fa2b80c68731567a6bb425405783f4464ecb2fdfc13b733df6dac` |

Also changed by the PR, not pinned: `agent/credential_auth_recovery.py` (`recover_auth_failure`),
`agent/credential_pool_pushed.py` (new `adopt_pushed_replacements`, `auth_wall_is_transient`),
`hermes_cli/turn_exit.py` (kanban exit code). `agent/agent_runtime_helpers.py` is unchanged.

## Findings

- `credential_pool._mark_exhausted` now stamps `extra["bench_token_fp"]` (fingerprint of the row's
  token at failure time) and rewrites the `failure_reason` handling as an equivalent comprehension.
  Status, cooldown and rotation semantics of `mark_exhausted_and_rotate` are unchanged, so the
  selected-attempt recovery wrapper's accounting (`credential_id=snapshot.entry_id`,
  `api_key_hint=snapshot.token`) behaves as before. `selection.py` never reads `entry.extra`, and
  eligibility (`_exhausted_until`, model cooldowns, `enabled_entries`) is untouched.
- `credential_auth_recovery.recover_auth_failure`: when rotation finds nothing, it now re-reads
  auth.json for auth-benched refresh-less OAuth rows, sets `pool._current_id` and calls
  `agent._swap_credential` on a pushed replacement. This is a new live-agent credential swap, but it
  is reached only through `recover_with_credential_pool` -> `_recover_auth_failure`. For a manual
  choice (`_account_usage_failed_attempt` set) the account-usage `recovery_wrapper` replaces that
  function entirely and never calls the original, so the new swap cannot bypass the request-local
  snapshot; the next attempt is still a fresh `choose_snapshot`. Unselected turns get the new
  behavior as normal pool recovery.
- `turn_recovery.nonretryable_client_error_result`: on an auth failure it adds
  `result["auth_pool_transient"]` from `auth_wall_is_transient`, a read-only check of the pool and
  auth.json (no `_current_id` change, no swap). `turn_exit` maps it to the kanban rate-limit exit
  code (75) instead of 78. No route or credential fence is added; nothing a snapshot must honor.
- No changes to the client factories, `recover_with_credential_pool`, `_failed_credential_identity`,
  `switch_model`, `interruptible_*_api_call`, or `perform_api_call`.
- `selection.py` is unchanged, so the recovery-wrapper composition digest is unchanged. All existing
  pins are kept for mixed-version rollout.
