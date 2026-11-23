# Logical-class-only delegation from managed Kanban lineage

Source patch ordinals: 5755–5757.

## Behavior

New delegation from a managed Kanban worker and its registered descendants accepts logical model classes, not caller-selected concrete models/providers, endpoints or transport overrides. Interactive parents retain their existing exact-pin behavior. Restriction lineage grants no creator or lease authority.

## How it works

A process-local weak identity map carries an unforgeable-in-request restriction token. Logical selectors for every sibling are staged before credentials or construction effects. The private `LogicalRequest` follows the selected route into the builder; missing or mismatched requests fail closed, and descendant construction registers the same restriction lineage.

Post-construction validation distinguishes caller/tier overrides from the allocator-selected route's own `custom_providers[].extra_body`: trusted routing keys are accepted only at the exact configured values, while caller keys remain refused even when equal. Named auth/destination keys in `extra_headers` and `extra_query` are checked case-insensitively. Failed late overlays close/detach the unstarted child.

## Code

- `agent/kanban_delegation_policy.py`
- `agent/managed_worker.py`
- `agent/subagent_lifecycle.py`
- `tools/delegate_tool.py`
- `tools/delegate_tool_routes.py`
- `run_agent.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/tools/test_kanban_delegation_no_pin.py`, `test_kanban_delegation_lineage.py` and `test_kanban_delegation_component79.py` cover registry/direct/lifecycle entry, sibling preflight before effects, inherited classes, descendant custody, malformed capabilities, late tier merges, exact trusted route bodies, listed auth keys, and interactive compatibility. Origin-custody suites under `tests/agent` and `tests/hermes_cli` cover separation from native creator authority.

## Applying

Apply policy, route staging, builder and lifecycle changes as one dependency chain. Callers specify a supported logical class and let normal allocation resolve the concrete route.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

The token is not serialized or accepted from environment/request fields. Auth-name refusal is a finite list, not a universal credential detector. Allocator-owned credentials are unchanged. A registered descendant remains restricted even though registration itself supplies no native worker authority.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
