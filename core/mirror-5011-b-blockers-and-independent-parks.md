# Shared blockers and independent park recovery

Mirror entries: 4457, 4462, 4467, 4495, 4504.

## Behavior

- **4457** — GitHub REST core bucket names normalize to the shared `github-rest-quota` identity without merging unrelated outage names.
- **4462** — A park with its own remote-host probe keeps that wait when an unrelated shared blocker clears; its own successful wake is usable once.
- **4467** — Latest check results are reduced once within an existing commit/config-validated input scope, with private results for callers.
- **4495** — Blocker state copying, assertion-row sorting and missing-check restoration avoid repeated work for unchanged inputs.
- **4504** — A row-first remote re-park cannot reuse a spent older park and wake immediately.

## How it works

The independent-wait rule reads the current park payload's `wake_check` and `probe_host`; absence of a local check row does not mean absence of a probe. A later independent `condition_met` can release the card from an older wall until a claim consumes that release. Parks explicitly awaiting that wall remain tied to it.

Check-result memoization retains the winning reduction only under the existing validated scope, pins its timestamp, preserves tie ordering, and declines to publish the unavailable-config fallback. Blocker states copy only their mutable integer maps. Assertion rows remain sorted per held task mapping while lookback filtering stays per caller. Missing-check restoration batches newest-park IDs and skips only already-settled IDs.

Continuation state checks whether a prior park was consumed by a wake or a later run's claim/spawn. A superseded park has no reusable timed payload; the scheduled row waits for its own event. The wake path rechecks park identity in the write transaction.

## Code

- `hermes_cli/kanban_blocker_own_wait.py`
- `hermes_cli/kanban_blocker_rows.py`
- `hermes_cli/kanban_continuation.py`
- `hermes_cli/kanban_shared_blocker_checks.py`
- `hermes_cli/kanban_shared_blockers.py`
- `hermes_cli/kanban_wake_check.py`
- `scripts/kanban_reclaim_replay_bench.py`

## Tests

Source tests exercise bucket alias coalescing and successful check clearing; local and remote probes versus bare-clock waits; private-copy isolation, peer/local writes, rollback, alias/lookback changes and failed reductions; batched restoration and new parks; both import orders, same-run claims, transaction races and the missing-park backstop.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/hermes_cli/test_kanban_blocker_remote_probe_park.py`
- `tests/hermes_cli/test_kanban_g2e_reclaim_cost.py`
- `tests/hermes_cli/test_kanban_latest_check_memo.py`
- `tests/hermes_cli/test_kanban_remote_repark.py`
- `tests/hermes_cli/test_kanban_shared_blocker_bucket_ids.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

The missing-park backstop is one hour after the recorded parking reference, not the old park's deadline. An old run's own imported claim must not invalidate its park. These optimizations do not change sweep cadence, expiry reclaim, crash policy or quota budgets.
