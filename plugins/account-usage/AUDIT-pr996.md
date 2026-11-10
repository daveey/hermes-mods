# Core PR 996 compatibility audit

Candidate: `717a5f49600e3850b3b38872f55a41a9fbabb2c5`.

Matched the nearest already-accepted version of each file by SHA256 through
candidate history, then reviewed its complete diff to the candidate:

| File under `agent/` | Pinned baseline commit | Candidate SHA256 |
|---|---|---|
| `turn_api_call.py` | `f78fd0940f255372956fb25237c1ee5cf55a8152` | `bd629a747d91293c5e8f558a87d79e6bad6158f856fa7bdb662b7af3ecd92de3` |
| `credential_pool.py` | `1561500cd6aebe81cfbce1155d3adf85bdc172b0` | `3eccb7cbdb2e2bc4cefb4fa5f6f424f136f95ea6321f168033d0543334bbe315` |
| `turn_recovery.py` | `0d9d537f6a6d14dac1c295588faddcbed724d3f8` | `be0031da0ceafc8079bd4f0280c35716284ce2bbf56f3da3c6cd04df2fd963be` |
| `turn_response_check.py` | `1561500cd6aebe81cfbce1155d3adf85bdc172b0` | `6c0b65026eeb01ca50d2310ddab9d0ea688b375249d8b63a6fb76cce130e24ac` |

## Findings

- `turn_api_call`: only adds a warning after successful Nous-rate-limit fallback.
  `perform_api_call` and restart/route policy are unchanged.
- `credential_pool`: only adds provider identity to the throttled exhausted-pool
  log. Selection, exhaustion, rotation, model cooldown and credential fences are unchanged.
- `turn_recovery`: replaces `bool(_try_activate_fallback(...))` with an equivalent
  false branch, success warning, and `True` return for Codex app-server fallback.
  The activation call, classified reason, and pre-existing eligibility checks are preserved.
- `turn_response_check`: only logs invalid-response details after successful fallback.
  Retry limits, fallback activation, and restart behavior are unchanged.

The client factories, `recover_with_credential_pool`, `_failed_credential_identity`,
`switch_model`, interruptible API wrappers, and `perform_api_call` retain their
contracts. No new credential or route fence is introduced or bypassed by request-local
snapshots. Reviewed `selection.py`'s route/terminal checks and recovery wrapper:
no change is required, so its fast-rate-limit-fallback composition digest stays unchanged.
All previous execution digests are retained for mixed-version rollout.

## Verification

Using the installed dependency-environment Python 3.14.9 explicitly via
`HERMES_MODS_PYTHON`:

- `scripts/test-all -k account-usage -j 1`: account-usage and repo guards PASS
  against installed core `7f29054ff9801714fd1051bd8294c59a2bd12c4a`.
- Same suites with `--hermes-core <candidate>` and `HERMES_TEST_CORE=<candidate>`:
  both PASS.
- `python3 plugins/account-usage/check_compat.py --json`: exit 0, no drift or skipped checks.
- Same compatibility check with `--core <candidate>`: exit 0, no drift or skipped checks.

The runner's default bare managed interpreter initially lacked a committed PM dependency
environment. Selecting the actual installed environment resolved this test-launch issue;
no production code was changed to accommodate it.
Fresh-process Mac installation receipts are tracked separately as deployment evidence.
