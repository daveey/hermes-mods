# Bounded launcher preflight

Patches: 1284 1285 1286 1287. Source: fork commits tagged `fix(launch)`.

## Behavior

A slow Package Manager (PM) check no longer holds `hermes` commands hostage. On a loaded Mac mini, `prepare_launch` asked PM whether the venv was current with no deadline, and `check_runtime` repeated plugin-union/YAML work on every dispatch, so short `hermes kanban` calls exceeded 60 s. Now the currency/runtime probe has one monotonic 1 s deadline; no answer means "unknown", never current or stale. An unknown verdict skips repair and publication, leaves completion debt (and says so on stderr when an update is owed, once per launch: 1285), and still hands off once to the managed interpreter. `hermes update` (not its report-only forms) keeps the full unbounded check. Deferred runtime checks still report damaged store tools (1286). Windows keeps the old unbounded path.

## How it works

`hermes_cli/launch_probe.py` runs the probe in an isolated worker with a 1 s end-to-end budget and SIGKILLs its process group on every exit. The verdict (current/unknown) rides a same-pid one-shot env handoff (`venv_sync.exec_launch`) so the exec'd child neither re-probes nor waits a second deadline; it is withdrawn if exec fails. A current launch publishes launchers only when one is missing, dead or pinned to a stale same-store Python. `check_runtime` reuses the verdict, is bounded the same way and honors `HERMES_DISABLE_LAZY_INSTALLS`. 1286: the opt-out, already-current, timed-out and probe-error paths now report the store-tool problems returned by `activate(allow_incomplete=True)`. 1287: CI routing only (`launch_probe.py` mapped to the upgrade suite in `scripts/ci/classify_changes.py`; the gateway PM stub accepts activate options).

## Code

`hermes_cli/launch_probe.py` (new), `hermes_cli/venv_sync.py`, `hermes_bootstrap.py`, `scripts/ci/classify_changes.py`.

## Tests

`tests/hermes_cli/test_launch_preflight_bound.py` (blocked PM worker returns within the budget; opt-out does not probe; verdict reuse; deferral after a timed-out probe; exec handoff and failed-exec withdrawal; owed-update notice; update stays unbounded; opt-out still reports tools), `tests/hermes_cli/test_launch_probe.py` (worker results, import noise, malformed answers, invalid arguments), plus adjustments to `test_venv_current_launch.py`, `tests/pm/test_source_update_launch.py` and `tests/gateway/test_pm_activation.py`. The Windows path is untested. This mirror task did not execute them.

## Applying

Apply the four patches in order; 1285-1287 are fix-ups of 1284.

## Gotchas

The 1 s budget is deliberate: a legitimately slow check reports "unknown" and defers, so run `hermes update` to get the full check. No plugin involved.
