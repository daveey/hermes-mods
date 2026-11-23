# Kanban: frozen diagnostic actionability transport (mirror 5732–5734)

## Behavior
- Frozen diagnostic actionability commands are persisted with exact outbox bytes and canonical root/deadline/notice evidence through ACK loss and local cache loss. They are administrative only and confer no admission, status, lease, budget or release authority.
- Unissued diagnostics require the canonical capability; escalations additionally require the server-clock deadline and exact canonical root. Capability outages are memoized per selection; escalation root probes are bounded to 32 per selection with durable rotation across restart; network work stays outside writer transactions.

## How it works
`kanban_actionability_transport.py`, `kanban_decision_ask.py`, sync client/hub routes/wire and `kanban_sync_parent_order.py`.

## Code
- `hermes_cli/kanban_actionability_transport.py`
- `hermes_cli/kanban_decision_ask.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_hub_routes.py`
- `hermes_cli/kanban_sync_wire.py`
- `hermes_cli/kanban_sync_parent_order.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_kanban_actionability_transport.py` checks transactional reservation, exact replay after unknown/ACK, bounded durable eligibility rotation, and unrelated-work progress. `test_kanban_actionability_board.py::test_frozen_root_board_replica_deadline_escalation` targets a loopback Board fixture stack and checks canonical confirmation/replica ingestion without task/run mutation. Production producer activation, deployed Board compatibility and delivery to a live owning chat are untested.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Issued stream custody is unchanged; this is the Core sender side only.

## plugin?
No. Core change.
