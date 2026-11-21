# Fail-closed assignment repair authority history

Mirror entries: 5256, 5257, 5258. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Completing a local assignment repair requires matching authority history, not just a task row that may have arrived before its events. Supported reasonless block producers still work; a malformed nonstring reason does not become authority.

## How it works

The repair path reads both `blocked` and `block_loop_detected` history and validates the matching authority requirement against the original task. Missing matching history raises `AssignmentRoutingError`. Missing/null reasons are supported as no stated reason; nonstring non-null reasons refuse. The transport changes sharing entry 5256 are documented separately.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/kanban_assignment_routing.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_hub_transport.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Assignment-routing source tests reject absent/malformed authority history, accept reasonless block variants, and exercise completion from `block_loop_detected` history rather than only ordinary blocked events.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_kanban_assignment_routing.py`
- `tests/hermes_cli/test_kanban_sync_hub_proxy_refusal.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

Do not infer authority from assignment alone or treat replication delay as proof of permission. A reasonless block is not the same as malformed structured history.

## Why core instead of a plugin

Assignment repair completion is a core lifecycle authority decision; a plugin cannot safely validate it after the transaction has already completed.
