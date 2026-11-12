# Retiring failed-result observers stop only their own unit

Patches: **2853, 2854**.

## Behavior

A failed-result observer with unusable context or an import failure retires with exit 78 and queues a best-effort stop of its own systemd user unit. This also ends legacy transient units that lack `RestartPreventExitStatus=78`.

## How it works

- **2853:** `_retire` releases readiness state and calls `_stop_own_unit`. It accepts only a `hermes-failed-result-*.service` context name that exactly matches this process’s `/proc/self/cgroup` tail, then runs `--user --no-block stop` with a bounded subprocess.
- **2854:** resolve systemctl lazily through `hermes_platform.resolver.locate_command`, preserving any command prefix; a missing resolver/import is a no-op rather than masking retirement.

## Code

- `hermes_cli/kanban_failed_result_observer.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_kanban_failed_result_self_stop.py` covers import failure, malformed context, wrong cgroup/unit, non-observer service names, command failure, and missing command or cgroup. The resolver fixture is updated in 2854.

- `tests/hermes_cli/test_kanban_failed_result_self_stop.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

Linux/systemd only. A context unit name alone never authorizes a stop. Unexpected observation errors still use the manager’s restart behavior; only deterministic retirement gets this cleanup.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
