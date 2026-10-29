# Wake check confirms exit 0 before waking; the reaper no longer steals probe statuses

Patch: 0703 (daveey/hermes `ac43921555`). Extends [kanban-condition-wakes.md](kanban-condition-wakes.md).

## Behavior

- A wake-check probe that exits 0 is run a second time. The card wakes only if the second run also exits 0; the second result is what counts. A stray exit 0 no longer produces a `condition_met` event.
- The `condition_met` payload carries `diagnostics` (first rc and output tail, confirm rc, pid, host, cwd, HOME, first three PATH entries) so a false wake can be traced from the event alone.
- `reap_worker_zombies()` no longer calls `os.waitpid(-1, WNOHANG)`. It polls only the worker `Popen` handles that `_default_spawn` parks in `_live_worker_procs` (now on POSIX as well as Windows). The old call also collected wake-probe children, and Python 3.14's `Popen` then reported their exit as 0.

## How it works

`kanban_wake_check._probe_and_apply` re-runs the probe when `rc == 0` and passes `diagnostics` into `apply_result`. In `kanban_db_dispatch`, a negative returncode (killed by signal) is recorded as the signal number, otherwise through `_wait_status_from_returncode`.

## Code

`hermes_cli/kanban_wake_check.py`, `hermes_cli/kanban_db_dispatch.py`.

## Tests

- `tests/hermes_cli/test_kanban_wake_check.py`: `test_stray_exit0_does_not_wake_when_confirmation_run_fails`, `test_condition_met_payload_carries_diagnostics`, `test_reaper_does_not_steal_wake_probe_status`.
- Untested: the reaper's signal-exit encoding on POSIX for real workers, and Windows behavior of the now-shared `_live_worker_procs` path. This mirror did not re-run these tests.

## Applying

`git am -3` 0703 in series order; it edits the wake-check and dispatch modules from [kanban-condition-wakes.md](kanban-condition-wakes.md).

## Gotchas

- A passing probe now runs twice: probes with side effects or long runtimes pay double on success.
- The second run's rc goes through the normal `apply_result` rules (exit 1 keeps waiting); how a non-0/1 second rc counts toward `wake_check_broken` is untested.

## Plugin?

No. Core dispatcher internals.
