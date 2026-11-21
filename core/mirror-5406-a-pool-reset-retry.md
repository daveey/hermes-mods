# Retry through a known pooled-credential reset

Mirror entries: 5124.

## Behavior

- When every credential in a real pool is cooling down for the requested model with a known reset, rate-limit backoff waits to the earliest reset plus a three-second margin.
- Resets within the 660-second reset budget can replace a shorter fixed backoff; a reset beyond that budget ends retries immediately with a reset-naming quota refusal.
- No pool, a pool with room, or unavailable reset evidence keeps ordinary backoff behavior.

## How it works

`pool_reset_wait()` accepts only the real `CredentialPool` and derives evidence from `quota_unavailable()`. `compute_error_backoff()` selects a `pool_reset` wait for a positive reset inside the budget. `settle_unrecovered_error()` ends the cycle before sleeping for a reset past the budget, returning the structured `SubscriptionQuotaUnavailable` refusal. This changes retry timing, not credential selection policy.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `agent/model_routing_unavailable.py`
- `agent/turn_api_error.py`
- `agent/turn_recovery.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

`test_pool_reset_wait.py` uses real pool fixtures and controlled time to cover a reset just beyond the previous fixed wait, eventual credential selection, fixed retry-after precedence, pools with room/no pool, and immediate no-sleep termination beyond budget with the reset in the error. Actual provider OAuth refresh, network rate limits and long wall-clock sleep are untested here.

Source test/coverage paths:

- `tests/agent/test_pool_reset_wait.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

The budget comparison is against time until reset; the three-second margin is added to an admitted wait. Unknown evidence does not justify a guessed reset. This is not an unlimited quota wait or permission to bypass provider limits.

## Why core instead of a plugin

Only core retry settlement has the requested model, real credential-pool cooldown evidence and authority to end or delay a turn. A plugin timer cannot preserve that recovery contract.
