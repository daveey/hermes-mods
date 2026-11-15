# Shared blocker window reuse and event-order fences

Patches: **4258, 4278, 4296, 4326** (4352 snapshot).

## Behavior

Shared-blocker lanes can reuse one derivation walk within a sweep, with decoded run rows cached per task. A failed advisory verdict cannot turn an already committed unblock into failure or abort later bulk-unblock items.

Legitimate UUIDv7 timestamps minted just across a created_at second boundary remain usable for event ordering; backfill-like mismatches remain untrusted.

## How it works

Incremental assertion cohorts remain bounded to the caller's lookback window; live aliases and check/fence inputs still participate. The assertion scan gains a partial created_at index, retained across drift rebuilds, with a fallback for unmigrated boards.

A publication predicate binds the shared walk to the actual computed window IDs, preventing a peer commit between key acquisition and computation from caching a walk under the wrong window. Event-order consumers share the narrow straddle interpretation.

## Code

- `hermes_cli/kanban_blocker_inputs.py`
- `hermes_cli/kanban_blocker_rows.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_idle_candidates.py`
- `hermes_cli/kanban_shared_blocker_checks.py`
- `hermes_cli/kanban_shared_blocker_fence.py`
- `hermes_cli/kanban_shared_blockers.py`
- `hermes_cli/kanban_sync_divergence.py`
- `scripts/kanban_reclaim_replay_bench.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_blocker_input_config.py`
- `tests/hermes_cli/test_kanban_blocker_inputs.py`
- `tests/hermes_cli/test_kanban_blocker_rows.py`
- `tests/hermes_cli/test_kanban_shared_blocker_check_edges.py`
- `tests/hermes_cli/test_kanban_shared_blocker_checks.py`
- `tests/hermes_cli/test_kanban_shared_blocker_uid_precision.py`
- `tests/hermes_cli/test_kanban_shared_blockers.py`
- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`
- `tests/hermes_cli/test_kanban_sync_running_heal.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

A memo is not an admission verdict for other gates. Transaction rollback, older caller clocks, window expiry without writes and concurrent publication require conservative handling. Historical performance comments are not measurements from this pass.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
