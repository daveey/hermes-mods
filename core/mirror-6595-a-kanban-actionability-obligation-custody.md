# Kanban actionability: frozen obligations and administrative deadlines

Patches: 6003, 6004, 6132, 6133, 6134, 6135, 6136.

## Behavior

- Confirmed frozen root receipts are composed into describe/report: missing diagnostic deadlines are filled only from matching local confirmed home custody, and UNKNOWN is retained.
- Source-bound recovery obligations are frozen with canonical source clocks and immutable preview bytes; premises are compared before first issuance, uncertain issued commands replay exactly, and rejected custody stays final.
- New park and block sources freeze reviewed administrative-deadline policies (scheduled-wake grace, blocked admin review) at creation, journaling source bytes atomically with bounded home-only sync delivery.
- Live assignment 'blocked' events are accepted as todo sources (and superseded or historical human blocks are ignored).

## How it works

Modules under kanban_actionability_* (confirmed, sources, observation, receipt, recovery, transport, creation, intents, materialize, parents) implement readers and writers; a design doc ships. Same-clock source ordering is by append order, not UID text order.

## Code

Paths touched by these entries (non-test):

- `docs/actionability-recovery.md`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_actionability.py`
- `hermes_cli/kanban_actionability_confirmed.py`
- `hermes_cli/kanban_actionability_creation.py`
- `hermes_cli/kanban_actionability_intents.py`
- `hermes_cli/kanban_actionability_materialize.py`
- `hermes_cli/kanban_actionability_observation.py`
- `hermes_cli/kanban_actionability_parents.py`
- `hermes_cli/kanban_actionability_receipt.py`
- `hermes_cli/kanban_actionability_recovery.py`
- `hermes_cli/kanban_actionability_recovery_cli.py`
- `hermes_cli/kanban_actionability_sources.py`
- `hermes_cli/kanban_actionability_transport.py`
- `hermes_cli/kanban_db_events.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_sync_client.py`

## Tests

Regression test-path inventory from these commits; path presence is not behavior proof. Test bodies were not audited and **tests were NOT executed**:

- `tests/hermes_cli/test_kanban_actionability_confirmed_reader.py`
- `tests/hermes_cli/test_kanban_actionability_contract.py`
- `tests/hermes_cli/test_kanban_actionability_creation.py`
- `tests/hermes_cli/test_kanban_actionability_creation_boundaries.py`
- `tests/hermes_cli/test_kanban_actionability_intents.py`
- `tests/hermes_cli/test_kanban_actionability_materialize.py`
- `tests/hermes_cli/test_kanban_actionability_observation.py`
- `tests/hermes_cli/test_kanban_actionability_parent_snapshot.py`
- `tests/hermes_cli/test_kanban_actionability_recovery.py`
- `tests/hermes_cli/test_kanban_actionability_recovery_cli.py`
- `tests/hermes_cli/test_kanban_actionability_recovery_reader.py`
- `tests/hermes_cli/test_kanban_actionability_source_order.py`
- `tests/hermes_cli/test_kanban_actionability_sync_delivery.py`
- `tests/tools/test_kanban_actionability_creation_entry.py`
- `tests/tools/test_kanban_tools.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Producer and reader kind allowlists must stay in lockstep.
- Idle retry writes were removed for unavailable adapters.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes Kanban task creation, events and sync inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
