# Failed-result observer: bounded restarts, retire on deterministic failure, tempdir released

Patches: **2628, 2643**.

## Behavior

The failed-result observer is a transient systemd user unit armed by `arm_service`. It waits for a worker's failed result to commit, then reconciles it. Before these patches a deterministic startup failure (for example an import error from a wrong interpreter or a broken checkout) restarted it every 500 ms with no limit, about 1.7k journal lines per 5 minutes per unit.

- The observer exits with status 78 (`EX_CONFIG`) on an `ImportError` or on a missing or malformed context argument. The unit lists 78 in `RestartPreventExitStatus`, so the manager retires it instead of restarting.
- Any other repeated crash restarts at most 10 times per 300 s (`StartLimitBurst=10`, `StartLimitIntervalSec=300s`), then the unit fails and `--collect` removes it. The dispatcher sweep still reconciles committed failed results, so a retired observer loses no result.
- The readiness tempdir (`failed-result-*/ready`, next to the board database) is now released on retire (exit 78) and after every stop, including the last one before the start limit hits. Before, only a successful observation removed it.
- A restarted observer recreates the tempdir before it acknowledges readiness.

## How it works

- 2628: `kanban_failed_result_observer.main(argv)` replaces the inline `__main__` block. It validates that the context is a dict with `db` and the keys `db`, `task_id`, `run_id`, `claim_lock`, and returns `STARTUP_FAILURE_EXIT` (78) with a stderr line otherwise. It also returns 78 when `observe` raises `ImportError`. Other exceptions propagate and exit nonzero, which restarts the unit within the budget. `kanban_failed_result_service.arm_service` adds `RestartPreventExitStatus=78`, replaces `StartLimitIntervalSec=0` with the finite limit, and adds `StartLimitBurst`.
- 2643: `_release_ready(context)` removes the ready file and its directory, best effort. It touches only a path shaped `failed-result-*/ready`, and OS or context errors are swallowed so cleanup never changes the exit status. `main` calls it on both exit-78 paths and after a successful observation. For crashes and start-limit exhaustion, `_ready_cleanup_args` adds `ExecStopPost=-rm -f -- ${VAR}` and `ExecStopPost=-rmdir -- ${VAR}`, with the paths passed through `--setenv` so spaces, `%` and `$` in board paths need no quoting. If `rm` or `rmdir` cannot be located, the unit keeps its previous behavior. `observe` recreates the directory (`mkdir(exist_ok=True)`) before writing the ack file.

## Code

`hermes_cli/kanban_failed_result_observer.py`, `hermes_cli/kanban_failed_result_service.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_failed_result_observer.py` (2628: the armed unit's restart properties are bounded; bad context and import failure return the prevent status; other observer errors still propagate; a real subprocess entrypoint with an import failure exits 78) and `tests/hermes_cli/test_kanban_failed_result_retire_cleanup.py` (2643: tempdir released on both retire paths; release never masks the exit status and touches only the `failed-result-*/ready` shape; a restarted observer recreates the directory; the armed unit carries the `ExecStopPost` and `--setenv` arguments; missing `rm`/`rmdir` keeps the previous unit). Entry path: the observer's `main` and `observe` functions and `arm_service` with `systemd-run` replaced by a stub. **Untested**: a real systemd manager enforcing the start limit and running `ExecStopPost`; the 2643 commit message reports a manual check on systemd 255, which is not part of the test suite.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Needs a systemd user manager; without `systemd-run`, `arm_service` still raises as before.
- A retired or start-limited observer is not restarted; reconciliation of its failed result falls back to the dispatcher sweep.
- Unit properties are fixed at arm time; units armed before these patches keep the unbounded restart policy until they exit.

## Plugin

Core change; no plugin required.
