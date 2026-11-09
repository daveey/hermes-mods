# Compatibility audit: dd96d849794e

Candidate: `dd96d849794ee1611af49e4d35b1ca202ce56292`.
Accepted baseline for all three changed files: `1561500cd6aebe81cfbce1155d3adf85bdc172b0`, found by walking each file's history and comparing SHA-256 to the accepted pin lists.

| Core file | Accepted baseline digest | Added candidate digest |
| --- | --- | --- |
| agent/client_lifecycle.py | f2d95a156436e58b02ef1c3aae47db6a820097759559a8d6f9fb77cfabfb0070 | 0e06ebd1c7f6582f4fcdb17e5260dd0ba0809dccf769ddb55b046485a38ec304 |
| agent/agent_runtime_helpers.py | a6da6effae04d9402e4cdc02c893cc85c7169e86193ba00a7f88f677d217628f | 67de0f1a1e45881d9cd4d0eed6455d7a3fbd1dcb2d7691cc2716696ad76bafea |
| agent/turn_recovery.py | 33b8b5e71c48ab50bebea3ec3552ab6b1e8375a0c887e936912a1e33b68a549d | ea766caabc2419e0f41329f39c4dc1fa09f2d7b40b2ce815e6201c926a2170a4 |

The Python 3.14 AST digest added for `agent.turn_recovery:route_classified_error` is `d7112c4d2185d18e8db578b5aaf8cf8442d97da4d3447d792bda3ff9ae8bcb2f`.

## Semantic review

- Client lifecycle only checks that an image part's `type` is a string before set membership. This avoids an unhashable-value error; request client factories and ownership/close behavior are unchanged.
- Runtime helpers now propagate a provider profile's `create_client` error when its auth type is `external_process`, rather than falling back to an HTTP client. Manual snapshots accept only `anthropic` and `openai-codex`; external-process routes are not admitted. Unselected requests use the original path. Neither `_failed_credential_identity`, `recover_with_credential_pool`, nor `switch_model` changes.
- Recovery adds `classified.should_fallback` to the terminal auth-failover branch. Account selection does not replace that route function. The peer fallback wrapper forwards the original classification unchanged and only adjusts the pool-availability hint for long-reset rate limits, so the new auth veto remains authoritative.
- The interruptible API-call seams and `perform_api_call` are unchanged and their existing pins still match. No new selected-credential eligibility or request-route fence needs mirroring. `selection.py` and its peer composition hash remain unchanged.

All existing accepted digests are retained for mixed-version rollout.

## Verification

Using the installed managed Python 3.14 test environment:

- `scripts/test-all -k account-usage --hermes-core <installed-core>`: PASS, including repo guards.
- `scripts/test-all -k fast-rate-limit-fallback --hermes-core <installed-core>`: PASS, including repo guards.
- `scripts/test-all -k account-usage --hermes-core <candidate-core>` with `HERMES_TEST_CORE=<candidate-core>`: PASS, including repo guards and the combined account-selection/fallback fixtures.
- `python3 plugins/account-usage/check_compat.py`: exit 0 for installed core `aaf655ac4b479f2ebfdb202dc5c7488ff36dd2f7`.
- The same compatibility check against candidate core: exit 0, no skipped pins.

Fresh-process host installation receipts are deployment evidence tracked separately; these tests do not claim already-running processes reloaded.
