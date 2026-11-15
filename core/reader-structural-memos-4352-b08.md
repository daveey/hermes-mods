# Kept board readers and incremental structural scans

Patches: **4257, 4259–4260, 4290, 4292, 4304–4305** (4352 snapshot).

## Behavior

Board read callers share a bounded process-wide connection pool, with at most two kept handles per board. Further concurrent borrowers use temporary handles instead of blocking on a busy kept slot.

Replica-reference lookups reuse readers and task-key scans. Dispatcher structural scans refresh changed task entries rather than rescanning unrelated task content.

## How it works

Pool reservations include opens in flight; eviction closes idle entries only and borrowed entries survive until release. Replica task-key proofs use feed sequence aggregates and connection generation; stale or unsequenced inputs remain unknown.

Task memos retain immutable cursor/mapping pairs, declare watched columns and event kinds, and publish only from trusted committed generations. Compare-and-swap prevents slower readers from moving the memo backwards. The follow-up records connection-open file identity so an old handle cannot poison a replaced file. Tests also cover WAL setup cleanup and module-owned callback lifetime.

## Code

- `hermes_cli/kanban_board_replica_refs.py`
- `hermes_cli/kanban_gone_board_handles.py`
- `hermes_cli/kanban_idle_candidates.py`
- `hermes_cli/kanban_kept_reader.py`
- `hermes_cli/kanban_lockguard.py`
- `hermes_cli/kanban_prose_refs.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_task_memo.py`
- `hermes_cli/kanban_wake_check.py`
- `scripts/kanban_reclaim_replay_bench.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_generation_reader_cache_registry.py`
- `tests/hermes_cli/test_kanban_kept_reader.py`
- `tests/hermes_cli/test_kanban_kept_reader_pool.py`
- `tests/hermes_cli/test_kanban_origin_snapshot_wal.py`
- `tests/hermes_cli/test_kanban_replica_refs_kept_readers.py`
- `tests/hermes_cli/test_kanban_task_memo.py`
- `tests/hermes_cli/test_kanban_watcher_idle_work.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Borrowed readers must not outlive their scope. Transactions can read current state but must not seed rollback-derived memos. New files, raw unmanaged connections, missing coverage and generation changes take conservative full paths. Benchmarks ship but were not run here.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
