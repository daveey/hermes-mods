# Aggregate-bus discovery and superseded-unit retirement deadlines

Patch coverage: 6731, 6732, 6733, 6810.

## Behavior

- Aggregate (pool) setup that runs `systemctl --user` from a dispatcher shell without user-bus environment now passes the environment from the existing verified same-UID user-bus resolver (`tools.process_registry.systemd_user_bus_env`) to `systemctl`, evaluated per attempt. The ownership/writable-directory checks themselves are pre-existing resolver code, not added by this series; the series' production change is the single `env=` argument and tests pin the resolver's refusals and the retry recovery.
- Retiring a superseded always-on unit no longer discards the persisted retirement deadline when the stop request failed, raised, was merely accepted (`--no-block`), or a status query showed it still deactivating or unknown. The deadline survives until the unit is observed `inactive` or `failed`; `deactivating`/unknown states stay tracked for a later poll, and the log line now says the stop was requested rather than that the unit was retired.
- A test fixture isolates the retry sleep from real subprocess polling.

## How it works

`hermes_cli/kanban_aggregate_scope.py` reuses the restart-safe scope probe's environment resolver; `hermes_cli/always_on_rollover.py` (`_unit_state`, `unit_active`, `_request_stop`) shares one checked stop submission.

## Code

`hermes_cli/kanban_aggregate_scope.py`, `hermes_cli/always_on_rollover.py`.

## Tests

Read from source; **not executed here**. `test_kanban_aggregate_bus.py`, `test_kanban_aggregate_scope.py`, `test_always_on_reap_retry.py` (ownership independent for directory and socket; recovery when the socket appears between retries). Real systemd user managers are **untested**.

## Applying

Apply in ordinal order.

## Gotchas

- A socket seen without owner validation must not be accepted.

## Why core, not a plugin?

Core service scoping.
