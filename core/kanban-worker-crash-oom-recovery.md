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
