# Attested remote-access placement for ssh and hub-read cards

Patches: **2370–2375, 2379**.

## Behavior

A worker's Board labels can attest restricted remote access (a Mac ssh key, hub-read). Cards that explicitly declare the needed access become portable to a pool whose every member carries the labels instead of pinning home; other home markers (launchd, cron, systemctl, gateway restart, hub paths) still bind, and unattested access keeps its home marker. The contract is carried through worker replicas, revalidated at claim/execution, and malformed replicated contracts are refused rather than turned into empty requirements. Producer guidance requires a concrete locality reason before pinning a hub helper (2379). When the wait-check hits a restricted-key SSH refusal it reads the owner replica instead (2370).

## How it works

2371–2372 inferred labels from prose; 2373 replaces that with explicit task `requires` declarations in `kanban_placement_coordination` (`access_requirements`, `check(..., requires=)`, `access_text`) and tool schemas, never installing credentials or granting writes. 2375 validates replicated contract shape. `kanban_wait_check` treats only an explicit forced-command refusal as unavailable transport, keeping stale/missing/transport outcomes distinct.

## Code

`hermes_cli/kanban_placement.py`, `kanban_placement_coordination.py`, `kanban_capability_routing.py`, `kanban_wait_check.py`, `tools/kanban_tools_schemas.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_coordination_placement.py`, `test_kanban_declared_access.py`, `test_kanban_access_contract_shape.py`, `test_kanban_wait_forced_command.py`, `tests/tools/test_kanban_helper_placement_guidance.py`.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Operators install the restricted keys and labels by hand; this change does not. After 2373, prose mentions no longer exempt home markers; only declared access does.

## Plugin

Core change; no plugin required.
