# Durable origin adoption of an existing task

Patches **0385, 0386, 0388** (`b9b85d1009`, `16633d1e41`, `5a1eeb73db`).

## Behavior

The owner creation adapter can attach an origin receipt intent to an exact existing owner-qualified task without recreating it or changing its status, worker/session, claim, schedule, parents or blocker state. The CLI `origin-adopt` and registered `kanban_origin_adopt` tool expose that operation. Adoption is durable binding intent, not completion, publication or delivery acknowledgement.

A retry of the same request and immutable intent returns the same task. Reusing a request ID with changed owner/origin/task data fails before task lookup; a second request for an already-bound owner/origin pair directs the caller to retry the existing request. Create and adopt cannot silently reuse one another's request IDs.

## How it works

`adopt_with_origin` validates the exact owner and board, checks immutable request identity, verifies the existing task and persists its marker in `origin_creation_intents` inside one board transaction. The trusted owner adapter verifies effective gateway/profile/database authority. Publication and origin acknowledgement remain separate gates.

## Code

- `hermes_cli/kanban_origin_creation.py`: atomic state-preserving adoption and immutable retry checks.
- `hermes_cli/kanban_origin_owner.py`: owner authority and operation dispatch.
- `hermes_cli/kanban.py`, `kanban_parser.py`, `tools/kanban_tools.py`, `kanban_tools_schemas.py`: CLI and registered tool entry paths.

## Tests

Executed on the applied public series: `tests/hermes_cli/test_kanban_origin_adoption.py` (12 passed) and `test_kanban_origin_owner.py` (33 passed). These cover state preservation, CLI/tool entry routing, owner checks, immutable request conflicts, missing-task ordering and replay. See [390-patch verification](mirror-390-verification.md) for the complete executed set and environment limits. No live courier, gateway or Desktop exercise is claimed.

## Applying

Apply the full ordered series from the documented merge base. These three patches precede archive-refresh **0389** and local-authority **0390, 0391, 0393, 0394**; they are not standalone against arbitrary upstream revisions.

## Gotchas

This adds a supported adoption primitive, not a migration of historical ownerless obligations. It neither retires ambiguous replica records nor proves their owner. No legacy migration or live reconciliation was performed by this mirror.

## Plugin relationship

Core owns the transactional intent and authority boundary. Plugins may call the registered action but cannot replace its lifecycle checks with a task-ID-only match.

## Follow-up patch (0774)

**0774** (fork `a8ebd66ee6`): the trusted origin courier selects the Kanban toolset around the real registry check and dispatch.

`receive` used `registry.get_definitions` against whatever toolsets the chat profile had enabled. A worker-only profile with no chat toolsets therefore returned `kanban_tool_unavailable` forever. `_dispatch_owner_tool` wraps `kanban_origin_adopt` and `kanban_create` in `scoped_kanban_toolset_selection(['kanban'])`. It does not write profile config or process env. A missing registration still returns `pending` / `kanban_tool_unavailable`. Child fences stay on the real dispatch path.

Tests in `tests/hermes_cli/test_kanban_origin_owner.py`:

- `test_real_cli_process_replays_persisted_creation` — empty-config subprocess replay of a persisted create.
- `test_courier_selection_is_scoped_and_preserves_child_fence` — selection is scoped; the child fence still refuses.

A live courier between two gateways is untested in this patch.
