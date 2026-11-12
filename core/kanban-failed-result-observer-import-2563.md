# Failed-result observer launches with a pinned import path

Patches: **2501**.

## Behavior

The failed-result observer is a detached reconciler process armed when a result write fails. On Linux it ran as a systemd user unit whose interpreter was a bare `sys.executable -m hermes_cli.kanban_failed_result_observer`. With a managed gateway the interpreter was the package-manager store Python (no third-party packages), the Hermes-owned `PYTHONPATH` having been stripped, so the unit died on `No module named 'ruamel'` and `Restart=on-failure` looped it indefinitely. The observer now starts with the checkout and committed dependency site-packages on `PYTHONPATH` and is launched with `-P`, on both the systemd path and the non-Linux subprocess path.

## How it works

`hermes_cli/kanban_failed_result_service.py` adds `observer_argv_env(env, context)`. It calls `pin_hermes_tree_on_pythonpath` and `hermes_module_argv` from `cron.scheduler_worker_env` (the same helpers the kanban and cron workers use) and returns the argv plus the pinned env. `arm_service` uses it to build the `systemd-run` command (its `--setenv` values come from that env), and `kanban_failed_result_observer.arm` uses it for the non-Linux child. See [kanban-failed-result-reconciliation-2491.md](kanban-failed-result-reconciliation-2491.md) and [kanban-worker-scope-failures.md](kanban-worker-scope-failures.md).

## Code

`hermes_cli/kanban_failed_result_observer.py`, `hermes_cli/kanban_failed_result_service.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_failed_result_launch_safety.py`. Entry path: `arm_service` with a stubbed `systemd-run`; the test then runs the unit's `ExecStart` interpreter with only its `--setenv` environment and imports `ruamel.yaml`, `hermes_yaml` and the observer (skipped if the base interpreter already imports ruamel). The non-Linux `arm` path and a real systemd unit are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Already-running crash-looping units must be replaced by a newly armed observer; this patch does not touch existing units.
- The observer launch now depends on `cron.scheduler_worker_env` helpers being importable.

## Plugin

Core change; no plugin required.
