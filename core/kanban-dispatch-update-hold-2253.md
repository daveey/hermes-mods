# Dispatch respects the core-update hold

Patches: **2216**.

## Behavior

CLI dispatch and dashboard dispatch refuse new ready/review claims while a live core-update drain hold exists. Existing workers continue; bookkeeping and promotion still run. CLI text and JSON explain the hold rather than only reporting zero spawns.

## How it works

The shared dispatch tick reads `agent.core_update_drain.active_hold` before spawn budgeting. `claim_if_admitting` checks again immediately before claiming, covering a hold that starts during the tick. Dead-writer holds do not block admission.

## Code

- `hermes_cli/kanban_db_dispatch.py`: tick gate and `DispatchResult.core_update_drain_hold`.
- `hermes_cli/kanban_gateway_drain.py`: shared hold reader and claim-time gate.
- `hermes_cli/kanban_ops.py`: CLI reporting.
- `hermes_cli/kanban_db_dispatch_spawn_call.py`: extracted spawn-signature compatibility helper.

## Tests

Source coverage, **not run in this mirror**: `tests/hermes_cli/test_kanban_dispatch_core_drain_hold.py` exercises CLI JSON and dashboard dispatch in both lanes, dead writers, CLI text, retained promotion, mid-tick holds and unavailable-module fallback.

## Applying

Use the [README full replay procedure](README.md#applying); this patch depends on the surrounding series, not an independent single-patch install.

## Gotchas

An unimportable drain module fails open, matching the existing gateway policy. Capacity and memory gates are unchanged. A hold stops claims, not already-running work.

## Plugin

Core dispatch enforcement; a plugin at one caller would not protect the other entry points.
