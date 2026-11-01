# Dependency blocks and links refuse an unresolved parent with a remote-wait remedy

Patches:

- `patches/1507-fix-kanban-refuse-unresolved-dependency-blocks-with-.patch` (`12950f6c5c`, `fix(kanban): refuse unresolved dependency blocks with a remote-wait remedy`)
- `patches/1508-test-kanban-model-the-nonlocal-review-checkpoint-par.patch` (`12949b1497`, `test(kanban): model the nonlocal review-checkpoint parent as a synced remote parent`), test-only

Related: [one dependency view for cross-gateway parents](mirror-0855-fix-kanban-sync-one-dependency-view-for-cross-gateway-parents.md) (`parent_states` and `kanban_sync_remote_parents`), [cross-gateway card wait](kanban-wait-replica.md) (the remedy the error names).

## Behavior

- **`block_task(kind='dependency')`** now refuses when any parent edge of the card resolves to no state at all: no local row, and no `kanban_sync_remote_parents` replica row. In that case `parent_states` returns `None` for that parent. Before this patch, such a dangling edge (for example `t_foreign` on another gateway, linked by raw SQL or before an eviction) ended the worker's run and parked the card behind a parent that nothing would ever finish. Now the block raises `ValueError` inside the write transaction, so the card, its run, its events and the block comment are all left unchanged and the worker keeps its claim. This applies from `running` and from an already `blocked` card.
- **The error text** (shared with `link_tasks`): `unknown task(s): <ids>; not on this board. For a parent on another gateway, unlink any dangling edge, then use kind='time', awaiting={kind:'card', ref:'<parent-id>', gateway:'<owner-gateway>'}. … do not create a local dependency edge.` `link_tasks` had raised only `unknown task(s): …` for a missing parent. It now gives the same remedy, and still creates no edge.
- **Still allowed.** A local parent blocks the card and later releases it as before. So does a synced foreign parent whose state the replica knows (`running` → the child goes to `todo`, and `done` in the replica → the child becomes `ready` on `recompute_ready`). Other block kinds are not affected.
- **1508** updates the existing review-checkpoint test. It had used a dangling `t_remote_missing` edge to stand for a nonlocal parent, which 1507 now refuses. The test now seeds `kanban_sync_remote_parents` (`running`), checks that `get_task` is `None` and `parent_states` is `running`, and still asserts that the `dependency_wait` event's `review_checkpoint.awaited` names the remote parent. The test is renamed `test_synced_nonlocal_parent_can_be_checkpointed`.

## How it works

The new module `hermes_cli/kanban_dependency_validation.py` holds `validate_dependency_block(conn, task_id, kind)`, which `block_task` calls right after loading the row, inside its `write_txn` and before assignment routing. It also holds `missing_dependency_error(ids)` and `would_cycle` (moved out of `kanban_db.py` unchanged, and re-imported by `link_tasks`). The check reuses `kanban_db.parent_states`, so "resolvable" here means exactly what the readiness logic means.

## Code

`hermes_cli/kanban_db.py` (`link_tasks`, `block_task`), `hermes_cli/kanban_dependency_validation.py` (new).

## Tests

From the patches; **not run for this mirror**:

- `tests/hermes_cli/test_kanban_missing_parent_refusal.py` (new): `test_link_missing_parent_explains_cross_gateway_wait`, `test_dependency_block_refuses_dangling_edge_without_ending_run[running|blocked]` (task, runs, events and comments are compared byte for byte), `test_local_dependency_still_blocks_and_releases`, `test_synced_parent_with_known_state_can_still_gate_dependency_block`.
- `tests/tools/test_kanban_missing_parent_refusal.py` (new): `test_missing_parent_tool_refusal[kanban_link|kanban_block]` (registered tool handlers return the remedy as `error`, and the card is unchanged).
- `tests/hermes_cli/test_kanban_dependency_review_history.py::test_synced_nonlocal_parent_can_be_checkpointed` (1508).
- Untested: the CLI `hermes kanban block --kind dependency` and `hermes kanban link` entries, and the REST link route.

## Applying

Apply in full-series order (`git am -3 --empty=keep`, [README.md](README.md) Applying). 1507 depends on `parent_states` and `kanban_sync_remote_parents` from the cross-gateway dependency series. Apply 1508 with or after 1507, or the old review-history test fails.

## Gotchas

- Existing dangling edges are not removed. A card that already has one cannot take a dependency block until the worker unlinks the edge (or asks a supervisor to), and switches to a `kind='time'` card wait as the error says.
- A synced parent counts as resolved whenever it has a replica row, even a stale one. This patch does not check how fresh the replica is.

## Plugin?

No. The refusal lives in core `block_task` / `link_tasks` transactions, which every entry path (tool, CLI, REST) goes through.
