# Route worker-node capability needs through the home gateway

Patches: 1150.

## Behavior

- A copied card on a worker node without a hub replica relays capability-owner requests to the card’s `created_on` home gateway and parks on the corresponding owner IDs.
- The home routes with its registry: a remote holder gets a create-task request, while a home-local holder gets an idempotent owner card pinned to that home machine.
- Recently lapsed holder records retry for `RELAY_RETRY_S`; stale records beyond that window refuse. A worker-local card without the registry returns `capability_registry_unavailable` rather than pretending no holder exists.
- Human-request validation refuses asking the user to run work and paste results merely because an agent route failed; worker placement guidance points at `needs=`.

## How it works

Capability routing reuses the owner request ID as the local idempotency key. The home’s owner-status probe routes the relayed request using the home registry. The block policy recognizes agent-route gaps rather than converting them into human obligations.

## Code

- `hermes_cli/kanban_capability_routing.py`
- `hermes_cli/kanban_block_policy.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_awaiting.py`

## Tests

Source regression coverage:

- `tests/hermes_cli/test_kanban_capability_relay.py`
- `tests/hermes_cli/test_kanban_capability_routing.py`
- `tests/tools/test_kanban_agent_relay_ask.py`

See [mirror-1157-verification.md](mirror-1157-verification.md) for the exact files freshly executed for this publication and their results. Files listed here are source coverage references, not a claim that every file was rerun.

## Applying

Apply the complete numbered series at the frozen merge-base with `git am -3 --empty=keep`. Earlier patches include a curated integration overlay; individual entries are not promised to install independently. See [the inventory](README.md) and [verification report](mirror-1157-verification.md).

## Gotchas

This requires a reachable home gateway and suitable holder capability. Stubbed routing tests do not establish a live remote grant or machine execution.

## Plugin?

No. This changes core lifecycle, admission, discovery, routing or its tests/workflows rather than a plugin-owned extension point.
