# Bounded launcher preflight

Patches: 1284 1285 1286 1287 1300. Source: fork commits tagged `fix(launch)`; 1300 is `642d2c6878` (fork PR #339).

## Behavior

A slow Package Manager (PM) check no longer holds `hermes` commands hostage. On a loaded Mac mini, `prepare_launch` asked PM whether the venv was current with no deadline, and `check_runtime` repeated plugin-union/YAML work on every dispatch, so short `hermes kanban` calls exceeded 60 s. Now the currency/runtime probe has one monotonic 1 s deadline; no answer means "unknown", never current or stale. An unknown verdict skips repair and publication, leaves completion debt (and says so on stderr when an update is owed, once per launch: 1285), and still hands off once to the managed interpreter. `hermes update` (not its report-only forms) keeps the full unbounded check. Deferred runtime checks still report damaged store tools (1286). Windows keeps the old unbounded path.

1300: a permission failure keeps its denied path across both process boundaries. Before, the launch probe worker and the PM worker each flattened a `PermissionError` into a message string, so the launcher saw a generic `RuntimeError` and could not tell the user which install-state file (for example `pm-runtime/.prepare.lock`) it may not write. Now the launcher gets a real `PermissionError` with `errno`, `strerror` and `filename`, which `install_state_permission_message` turns into an actionable message.

## How it works

`hermes_cli/launch_probe.py` runs the probe in an isolated worker with a 1 s end-to-end budget and SIGKILLs its process group on every exit. The verdict (current/unknown) rides a same-pid one-shot env handoff (`venv_sync.exec_launch`) so the exec'd child neither re-probes nor waits a second deadline; it is withdrawn if exec fails. A current launch publishes launchers only when one is missing, dead or pinned to a stale same-store Python. `check_runtime` reuses the verdict, is bounded the same way and honors `HERMES_DISABLE_LAZY_INSTALLS`. 1286: the opt-out, already-current, timed-out and probe-error paths now report the store-tool problems returned by `activate(allow_incomplete=True)`. 1287: CI routing only (`launch_probe.py` mapped to the upgrade suite in `scripts/ci/classify_changes.py`; the gateway PM stub accepts activate options). 1300: the probe worker's error reply may carry `permission_error` (`errno`, `strerror`, `filename`) when the exception is a `PermissionError` with a filename; `_valid` accepts exactly that extra key and shape, and `venv_sync._launch_currency` re-raises it as `PermissionError`. In PM, `pm/worker.py` adds `errno`/`strerror`/`filename` to any `OSError` reply with a filename, and `pm/client.py` `_raise_worker_error` rebuilds an `OSError` subclass from them.

## Code

`hermes_cli/launch_probe.py` (new), `hermes_cli/venv_sync.py`, `hermes_bootstrap.py`, `scripts/ci/classify_changes.py`; 1300 also `pm/worker.py`, `pm/client.py`.

## Tests

`tests/hermes_cli/test_launch_preflight_bound.py` (blocked PM worker returns within the budget; opt-out does not probe; verdict reuse; deferral after a timed-out probe; exec handoff and failed-exec withdrawal; owed-update notice; update stays unbounded; opt-out still reports tools), `tests/hermes_cli/test_launch_probe.py` (worker results, import noise, malformed answers, invalid arguments), plus adjustments to `test_venv_current_launch.py`, `tests/pm/test_source_update_launch.py` and `tests/gateway/test_pm_activation.py`. The Windows path is untested. This mirror task did not execute them.

1300 adds `test_currency_permission_error_keeps_denied_path` (`tests/hermes_cli/test_launch_probe.py`: a real probe worker opens a read-only lock file; `_launch_currency` raises `PermissionError` with errno 13 and that path) and `test_permission_error_survives_worker_with_denied_path` (`tests/pm/test_worker.py`: the same through `client.sync_venv` and the PM worker). Both are POSIX-only. Run by this mirror pass in a detached worktree at `7fbf27b309`: `bash scripts/run_tests.sh -j 2 tests/gateway/test_rehome_cancel_lock.py tests/hermes_cli/test_launch_probe.py tests/pm/test_worker.py` gave 49 passed, 0 failed, 1 skipped (platform-gated) on Linux. No RED run on the parent was made here.

## Applying

Apply the four patches in order; 1285-1287 are fix-ups of 1284. 1300 needs 1284 (it edits `launch_probe.py`).

## Gotchas

The 1 s budget is deliberate: a legitimately slow check reports "unknown" and defers, so run `hermes update` to get the full check. No plugin involved.
