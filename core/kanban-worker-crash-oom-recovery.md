# Kanban worker crash recovery: scope-killed workers and typed OOM recovery

Patches **0438** (`4553b4f464`) and **0454** (`1c1cffe8d6`).

## Behavior

- **0438.** A worker killed by its systemd scope still counts as a crash for continuation, diagnostics, model tier
  and shared-blocker logic: the consumers share `FAILED_RUN_OUTCOMES = (crashed, oom_killed, signaled, timed_out,
  gave_up, failed)` instead of matching only `crashed`.
- **0454.** After consecutive `oom_killed` runs the next scope gets `MemoryMax = base * 2**n`, capped at 12 GiB and
  admitted only if it fits `MemAvailable` minus a 4 GiB host reserve. Workers get toolchain env defaults
  (`WORKER_TOOLCHAIN_ENV`, node heap `--max-old-space-size=3072`). More than 5 OOM kills in 24 h open one card per
  incident (`note_oom_kill`).

## Code

`hermes_cli/kanban_worker_scope.py`, `kanban_db_dispatch.py`, `kanban_continuation.py`, `kanban_diagnostics.py`,
`kanban_model_tier.py`, `kanban_shared_blockers.py`.

## Tests

`tests/hermes_cli/test_kanban_crash_outcome_consumers.py`, `tests/hermes_cli/test_kanban_oom_recovery.py`.

## Applying

0438 then 0454.

## Gotchas

The raised cap applies only to systemd-scope workers; the 24 h acceptance (fewer than 3 OOM kills) is a runtime
property, not covered by the tests.

## Plugin?

No.

## Follow-up patch (0570)

**0570** (source `8b741ea26b`): the first run of a worker scope gets `MemoryMax` at 2x the base, bounded by admission (the same `MemAvailable` minus reserve check as recovery); typed OOM recovery doubles from that value. `hermes_cli/kanban_worker_scope.py`; test `test_first_run_base_uplift_is_admission_bounded` in `tests/hermes_cli/test_kanban_oom_recovery.py`.

**0594** (source `529b6c9f66`), test-only: `test_managed_gateway_worker_is_spawned_in_restart_safe_scope` pins `hermes_cli.kanban_worker_scope.mem_available_bytes` to 16 GiB and expects the doubled first-run cap (`MemoryMax=1073741824`). 0570 updated `test_kanban_oom_recovery.py` but not this Linux-only sibling, so a host with enough free memory failed the old 512 MiB assert. Skipped on macOS (untested there).
