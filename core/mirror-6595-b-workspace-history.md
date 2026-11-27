# Host-local workspace binding and prior-home history recovery

Patch coverage: 6320, 6321, 6325, 6326, 6327.

## Behavior

Pickup eviction retains rows carrying explicit host-local dir/worktree source bindings so reimport cannot silently erase their local custody. Missing sticky bindings fail closed instead of resolving another worktree or adopting a foreign created-event path.

After rehome, missing published pre-handoff events and real run evidence can be recovered without treating current task ownership as proof of complete historical custody.

## How it works

Legacy workspace checks distinguish intentionally unbound worktrees from paths that were explicitly bound at creation. Historical path text is diagnostic only: it is not expanded or authorized on this host. Source-protocol grants retain their separate path.

Prior-home recovery requires matching server-attested rehome events locally and in the replica, agreeing current ownership and distinct source/destination. It fills only eligible pre-boundary gaps using negative local history ids, avoids notification-cursor replay and leaves the current task untouched. Tombstones, unresolved run custody, foreign authority and post-handoff publications retain normal fences/transport. Automatic repair does not restore deliberately pruned terminal inventory.

## Code

Affected implementation and integration paths in the source series:

- `hermes_cli/kanban_missing_workspace.py`
- `hermes_cli/kanban_pickup_history.py`
- `hermes_cli/kanban_rehome_history.py`
- `hermes_cli/kanban_source_dispatch.py`
- `hermes_cli/kanban_sync_repair_content.py`
- `hermes_cli/kanban_worker_node.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_kanban_workspace_path_loss.py`
- `tests/hermes_cli/test_kanban_workspace_source.py`
- `tests/hermes_cli/test_kanban_rehome_history.py`
- `tests/hermes_cli/test_kanban_rehome_history_guards.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

Directory existence is not source custody. A synced absolute path must never become local execution permission. Negative local event ids are import placement, not canonical spend ordering; consumers use timestamp/UID order. Recovery cannot overwrite unresolved real runs or fabricate missing authority.

## Why core, not a plugin?

Host-local source columns, pickup eviction and event/run repair are core custody mechanics, not plugin metadata.
