# Kanban worker crash recovery: scope-killed workers and typed OOM recovery

Patches **0444** (`4553b4f464`) and **0460** (`1c1cffe8d6`).

## Behavior

- **0444.** A worker killed by its systemd scope still counts as a crash for continuation, diagnostics, model tier
  and shared-blocker logic: the consumers share `FAILED_RUN_OUTCOMES = (crashed, oom_killed, signaled, timed_out,
  gave_up, failed)` instead of matching only `crashed`.
- **0460.** After consecutive `oom_killed` runs the next scope gets `MemoryMax = base * 2**n`, capped at 12 GiB and
  admitted only if it fits `MemAvailable` minus a 4 GiB host reserve. Workers get toolchain env defaults
  (`WORKER_TOOLCHAIN_ENV`, node heap `--max-old-space-size=3072`). More than 5 OOM kills in 24 h open one card per
  incident (`note_oom_kill`).

## Code

`hermes_cli/kanban_worker_scope.py`, `kanban_db_dispatch.py`, `kanban_continuation.py`, `kanban_diagnostics.py`,
`kanban_model_tier.py`, `kanban_shared_blockers.py`.

## Tests

`tests/hermes_cli/test_kanban_crash_outcome_consumers.py`, `tests/hermes_cli/test_kanban_oom_recovery.py`.

## Applying

0444 then 0460.

## Gotchas

The raised cap applies only to systemd-scope workers; the 24 h acceptance (fewer than 3 OOM kills) is a runtime
property, not covered by the tests.

## Plugin?

No.

## Follow-up patch (0576)

**0576** (source `8b741ea26b`): the first run of a worker scope gets `MemoryMax` at 2x the base, bounded by admission (the same `MemAvailable` minus reserve check as recovery); typed OOM recovery doubles from that value. `hermes_cli/kanban_worker_scope.py`; test `test_first_run_base_uplift_is_admission_bounded` in `tests/hermes_cli/test_kanban_oom_recovery.py`.

**0593** (source `529b6c9f66`), test-only: `test_managed_gateway_worker_is_spawned_in_restart_safe_scope` pins `hermes_cli.kanban_worker_scope.mem_available_bytes` to 16 GiB and expects the doubled first-run cap (`MemoryMax=1073741824`). 0576 updated `test_kanban_oom_recovery.py` but not this Linux-only sibling, so a host with enough free memory failed the old 512 MiB assert. Skipped on macOS (untested there).

## Follow-up patch (0871)

**0871** (fork `11975e798c`): `note_oom_kill` takes the reclaim connection. Opening a second connection inside the dead-worker write transaction deadlocks when that connect runs the legacy-inflight backfill: its `BEGIN IMMEDIATE` waits on the lock the caller already holds, for the busy timeout. `_classify_dead_worker` / `_reclaim_dead_workers` pass `conn`. Without a caller connection, `note_oom_kill` still opens its own. The alert card's `create_task` / `add_comment` nest as a savepoint on the passed connection. Never raises.

Code: `hermes_cli/kanban_worker_scope.py` (`note_oom_kill(conn=)`), `hermes_cli/kanban_db_dispatch.py` (`_classify_dead_worker`, `_reclaim_dead_workers`).

Test: `test_oom_reclaim_does_not_take_a_second_write_txn` in `tests/hermes_cli/test_kanban_crash_outcome_consumers.py`. It seeds the legacy-inflight backfill and calls `detect_crashed_workers` with an oom-kill classification; before the fix that path took a second write transaction from `_backfill_legacy_inflight_runs`. Ran on fork `origin/main` `a65917ae05` for this mirror: passed (1 case, in the 31-passed run recorded in [cron-finalize-slow-write.md](cron-finalize-slow-write.md)).
