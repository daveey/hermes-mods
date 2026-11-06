# Upstream refusal-history compatibility audit

Candidate core: `b513e412e96a6ff63b5ed356ac2b154e80446439`.

The nearest accepted file versions were found by SHA-256 over each file's Git history:

| File | Accepted commit | Accepted SHA-256 | Candidate SHA-256 |
|---|---|---|---|
| `agent/turn_recovery.py` | `53848e9009b3b113b1650d5f35835fae219d2c68` | `3e172094d47a20d3e7a1db0694cef57b946140dc167ac3f83275752487f8d678` | `620947745f25a511596e8afd8857389355122c0b15b7e45de0809453c1da1003` |
| `agent/turn_api_error.py` | `21acd343ff672213ecfabfc3ccf41c96a7337526` | `684686bbedef9039ee61d0ee22e53df94b0fed51fc9e6033ae9bf98032219196` | `7f52eec96d07b73ab0d5e58c0f3789f66aa29049dfde9b899562e77a83a283e8` |
| `agent/turn_response_check.py` | `0d0ee3a326d8666efdda877524e9ef3f7c6bddee` | `b6a0f97d43b08c0d87019babe5de4f2bd440bbc484061c4ca467b0d7b347ffc1` | `44d77d7801cfb2181c632afedff892ccf44b433f64f69cfa5a708afa5c7d4f4d` |

## Findings

- `turn_api_error` forwards `current_turn_user_idx` to `nonretryable_client_error_result`.
- `turn_response_check` forwards the same boundary to the response-refusal handler.
- `turn_recovery` adds that optional argument. Its terminal content-policy branch now returns pre-refusal history and turn boundary/action metadata before session persistence, instead of persisting the refused conversation first.
- No changes to the client factories, `recover_with_credential_pool`, `_failed_credential_identity`, `switch_model`, `interruptible_*_api_call`, or `perform_api_call` contracts are introduced by these diffs. No new credential or route fence is introduced.
- The selected-attempt recovery wrapper already declines content-policy failures: its allowlist is rate limit, billing, auth and model entitlement only. Request-local snapshots therefore do not divert content-policy errors into credential rotation or bypass the updated terminal refusal branch.
- `selection.py` is unchanged; the peer recovery wrapper composition digest remains unchanged. All existing pins are retained for mixed-version rollout.

## Verification

Installed Python 3.14.9, installed core `60aadf3280759a8203c7bb9fa8834d6bb8b73901`: direct targeted pytest run passed **283 tests and 72 subtests**. `check_compat.py` returned exit 0 on installed core and on the exact candidate, with no drift or skipped pin checks on the candidate.

The aggregate `scripts/test-all -k account-usage` invocation failed in this worker's scrubbed environment; the direct targeted invocation under the installed runtime and runtime dependency site passed. This is not a claim that the aggregate runner passed.

Host deployment and fresh-process installation are separate rollout checks, recorded in the task handoff rather than inferred from these file hashes.
