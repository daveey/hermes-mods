# Preserve pinned gateway checkouts during updates

Patches: **2233–2238**.

## Behavior

An update leaves gateways proven to run another checkout alone instead of pausing or restarting them onto the same pin. The boundary also applies to manual-process sweeps, restart-abort recovery and multiplex migration/resume. Repeated pause refusals by the same gateway acquire a durable agent repair card from the third consecutive deferral.

## How it works

Shared code-root classification supplements home ownership. Plain module launches follow working-directory/PYTHONPATH import order. Recovery resolves live gateway PIDs from inventory, then control socket, then status record; reused status-record PIDs must still identify a gateway. Pinned profiles count as covered, not stale survivors. Lookup failures preserve ordinary recovery behavior rather than breaking abort handling.

Per-install deferral state records refusing gateway names. Successful pauses clear streaks; gateways absent from the next refusal lose their streak. A fresh interpreter files or reuses a repair card per install/gateway, refreshing its evidence every twelfth deferral. Bookkeeping cannot change the update verdict.

## Code

`hermes_cli/update_fleet_scope.py`, `update_receipt.py`, `update_cmd_fleet.py`, `update_cmd_posix_pause.py` and `update_pause_deferrals.py`; migration guards in `gateway_migrate_guards.py` and `gateway_migrate.py`.

## Tests

Source coverage, **not run in this mirror**:

- `tests/hermes_cli/test_update_pause_pinned_runtime.py`: pinned systemd pause/restart, recovery/manual partitioning, import order, failing lookups, inventory/socket identity, migration guard and two pinned gateways in one pass.
- `test_update_pause_deferrals.py` in the same directory: threshold, resets, owner replacement, filing failure and supervisor refusal names.
- `test_gateway_migrate_multiplex.py`: update-hook and explicit resume refuse pinned gateways; unpinned resume control.

## Applying

Use the [README full replay procedure](README.md#applying), including all recovery and migration follow-ups.

## Gotchas

Unknown code roots are not declared foreign. The initial pause/restart integration tests target systemd; no launchd pinned-runtime end-to-end proof is added. Recovery components are tested directly, not by forcing an exception through the entire restart-abort sequence. Repair filing never authorizes stopping in-flight work.

## Plugin

Core updater, recovery and migration safety; no plugin required.
