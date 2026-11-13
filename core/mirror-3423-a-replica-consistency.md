# Kanban replica consistency tests and lag guard

Patches: **2927, 2986**.

## Behavior
- Pinned by tests: when a replica read fails, the last-good execution memo keeps its run evidence; warm counts re-project an expired run without recomputing.
- A worker node no longer evicts and re-imports a ready card copy from a replica that is behind the gateway's own most recent push; the copy stays until the replica catches up or a lag entry expires. A crashed PR owner's card stays guarded (its handoff comment and reclaimed event are not lost).

## How it works
- 2927 only adds tests for the replica execution memo.
- 2986 adds `kanban_replica_lag` (a short-lived record of rows this gateway pushed) consulted by the worker-node pickup in `kanban_worker_node`, with the sync client recording pushes.


## Code
- `hermes_cli/kanban_replica_lag.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_worker_node.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_pickup_replica_lag.py`: `test_a_crashed_pr_owner_stays_guarded_when_the_replica_lags_its_push`, `test_a_lag_entry_expires`, `test_a_lagging_card_is_not_reimported_until_the_replica_catches_up`.
- `tests/hermes_cli/test_kanban_replica_execution_memo.py`: `test_last_good_preserves_run_evidence_when_replica_read_fails`, `test_warm_counts_reproject_expired_run_without_recompute`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
Lag entries expire (covered by `test_a_lag_entry_expires`), so a replica that never catches up eventually allows re-import.
