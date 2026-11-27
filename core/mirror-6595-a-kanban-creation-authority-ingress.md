# Kanban creation requires durable owning authority

Patches: 6081, 6082, 6083, 6084, 6085, 6106, 6121.

## Behavior

- Agent task creation requires a registered durable owning chat; courier, swarm and decomposition ingresses share the transactional requirement.
- Observer-only ancestry no longer satisfies the gate: owning provenance is preserved before creation inheritance.
- Headless self-work intake (no chat by design) creates again; its dedicated path is exempt.
- Test fixtures register real chats and the forwarding fixture models a synced owner.

## How it works

kanban_create_authority.require is called from kanban_db_creation/graph/swarm and tools/kanban_tools; kanban_creator_chats keeps owning relation distinct from inherited observers.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_create_authority.py`
- `hermes_cli/kanban_creator_chats.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_creation.py`
- `hermes_cli/kanban_db_graph.py`
- `hermes_cli/kanban_origin_creation.py`
- `hermes_cli/kanban_swarm.py`
- `tools/kanban_tools.py`

## Tests

Regression test-path inventory from these commits; path presence is not behavior proof. Test bodies were not audited and **tests were NOT executed**:

- `tests/hermes_cli/test_kanban_ask_resolved_check.py`
- `tests/hermes_cli/test_kanban_capability_create.py`
- `tests/hermes_cli/test_kanban_capability_routing.py`
- `tests/hermes_cli/test_kanban_cli.py`
- `tests/hermes_cli/test_kanban_cli_resolved_assignee.py`
- `tests/hermes_cli/test_kanban_courier_scope_authority.py`
- `tests/hermes_cli/test_kanban_create_entry_parity.py`
- `tests/hermes_cli/test_kanban_create_observer_authority.py`
- `tests/hermes_cli/test_kanban_create_profile_authority.py`
- `tests/hermes_cli/test_kanban_create_scope_authority.py`
- `tests/hermes_cli/test_kanban_create_session_stamp.py`
- `tests/hermes_cli/test_kanban_create_skill_validation.py`
- `tests/hermes_cli/test_kanban_creator_origin.py`
- `tests/hermes_cli/test_kanban_fairness_creation.py`
- `tests/hermes_cli/test_kanban_foreign_desktop_owner.py`
- `tests/hermes_cli/test_kanban_graph_identity.py`
- `tests/hermes_cli/test_kanban_held_machine_pin.py`
- `tests/hermes_cli/test_kanban_lane_classifier_transport.py`
- `tests/hermes_cli/test_kanban_llm_pacing.py`
- `tests/hermes_cli/test_kanban_model_tier.py`
- `tests/hermes_cli/test_kanban_no_pool_audit.py`
- `tests/hermes_cli/test_kanban_origin.py`
- `tests/hermes_cli/test_kanban_origin_adoption.py`
- `tests/hermes_cli/test_kanban_origin_creation.py`
- `tests/hermes_cli/test_kanban_origin_notify.py`
- `tests/hermes_cli/test_kanban_origin_owner.py`
- `tests/hermes_cli/test_kanban_placement.py`
- `tests/hermes_cli/test_kanban_placement_diagnostics.py`
- ... and 47 more paths in the same commits

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- The self-work exemption was a correction after the first enforcement refused it forever.
- Placement tests needed explicit chat registration; passive fake gateways must not initialize chat stores.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes Kanban task creation and tool ingress inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
