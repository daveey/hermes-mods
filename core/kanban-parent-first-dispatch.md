# Parent-first dispatch and unclaimed-parent notices

Patches 2069.

## Behavior

Dispatch prioritizes a parent with waiting todo children within its effective priority. A chat-owned child waiting at least 30 minutes on a parent ready and unclaimed that long gets one `dependency_parent_unclaimed` event per wait. `kanban_show` exposes an advisory queue hint.

## How it works

Shared `DISPATCH_ORDER` uses the maximum of the card and waiting children’s priority, then waiter presence, then age; stored priority is unchanged. Lane queries, default listing, worker candidates and queue position share it. The sweep runs before spawn-budget refusal; both notification formatters recognize the event.

## Code

`hermes_cli/kanban_db_dispatch_queries.py`, `kanban_parent_unclaimed.py`, `kanban_queue_position.py`, `kanban_worker_node.py`, `tools/kanban_show.py`, gateway and TUI notification modules.

## Tests

`tests/hermes_cli/test_kanban_parent_first_dispatch.py`: priority/order parity, worker candidates, queue position, once-per-wait notices, claimed/short/unowned exclusions, capped dispatch sweep, notifier registration and the show-tool hint.

Execution status is recorded in [mirror 2072 verification](mirror-2072-verification.md); listed regression cases alone are not an execution claim.

## Applying

Use the complete snapshot’s [README Applying recipe](README.md#applying), including its replay plan and policy. These patches depend on earlier core changes.

## Gotchas

Priority does not create capacity or bypass placement. Notifier registration tests are not end-to-end message-delivery proof. The hint suggests action; it does not move or execute the parent.

## Could this be a plugin?

No standalone plugin is supplied: this changes core dispatch/admission or turn-finalization wiring.
