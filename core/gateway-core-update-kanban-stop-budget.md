# Core-update Kanban-only stop shares one 60 s admission budget

Patches: 1288 1294.

## Behavior

When a core update restarts the gateway and only Kanban cards are still running, the gateway waits up to 15 s (`CORE_UPDATE_KANBAN_WAIT_S`), then stops with a fixed 40 s leash (`CORE_UPDATE_KANBAN_STOP_LEASH_S`) and no second graceful drain, so wait + stop + 5 s poll allowance fit one 60 s admission budget. Human or unknown work, a withdrawn update intent and ordinary restarts keep their normal budgets. 1288 is a refactor: the restart log line's text saying whether the Kanban cap is armed lives in the restart-wait mixin (`_restart_cap_state_text`).

## How it works

When the 15 s wait expires, `_core_update_kanban_cap_spent` is set. At `stop()` entry `_core_update_kanban_snapshot_stop_leash` snapshots `_stop_kanban_leash_s` (only if the cap is spent, a restart is requested, the update intent exists and no non-Kanban work is awaitable), so mid-stop changes cannot flip the budget. `gateway/restart.py` then returns drain timeout 0 from `effective_stop_drain_timeout` and caps `effective_stop_watchdog_delay` at the leash. A cancelled restart clears both flags.

## Code

`gateway/run_shutdown_restart_wait.py`, `gateway/run_shutdown.py`, `gateway/restart.py`, `hermes_cli/update_gateway_fence.py`.

## Tests

`tests/gateway/test_core_update_total_drain.py` (short leash after spent grace; normal budgets for other work; snapshot at stop entry; cancel clears state; real settle wait fits the leash), `tests/gateway/test_core_update_kanban_wait.py` (restart line states whether the cap is armed; unfenced intent is not a launcher fence). This mirror task did not execute them.

## Applying

Apply 1288 then 1294.

## Gotchas

The leash is a fixed number; callers subtract their own elapsed stop time. No plugin involved.
