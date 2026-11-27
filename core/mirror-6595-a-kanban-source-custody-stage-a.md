# Kanban source-custody base (stage A, inert)

Patches: 6037, 6038, 6039, 6040, 6041, 6042.

## Behavior

- Lands the source-custody base: pin store, barrier, refs, removal seam and publication helpers, with a CI guard against blocking opens and dereferencing ref mutations.
- Publication argv helpers reject mixed --repo/positional grammar, derive full branch refs, and check implicit push defaults against the actual push config.
- In opt-in mode, write_txn withholds its boundary_done callback unless it can prove the writer ended, preserving the original failure under interrupted cleanup.

**Inert:** the new custody base has no existing production entrypoint that creates its store or activates custody. The new modules import one another, and expose explicit setup helpers; those APIs are not an activation receipt.

## How it works

New modules kanban_source_barrier/pin_store/publication/refs/removal plus scripts/ci/upstream_sync_push_argv.py; kanban_db_connect carries the boundary change.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_source_barrier.py`
- `hermes_cli/kanban_source_pin_store.py`
- `hermes_cli/kanban_source_publication.py`
- `hermes_cli/kanban_source_refs.py`
- `hermes_cli/kanban_source_removal.py`
- `hermes_cli/kanban_source_store_schema.py`
- `hermes_cli/worker_drain_spawn_inventory.json`
- `scripts/check`
- `scripts/ci/upstream_sync_push_argv.py`

## Tests

Entry-path behavior above is source-reviewed but **untested in this review**. The files below identify source regression targets, not proof that this review exercised those entrypoints.

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/hermes_cli/test_kanban_write_txn_interrupt.py`
- `tests/hermes_cli/test_kanban_write_txn_no_retry.py`
- `tests/hermes_cli/test_source_custody_barrier.py`
- `tests/hermes_cli/test_source_custody_objects.py`
- `tests/hermes_cli/test_source_custody_publication.py`
- `tests/hermes_cli/test_source_custody_removal_busy.py`
- `tests/hermes_cli/test_source_custody_store.py`
- `tests/scripts/test_code_health_cli.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Inert integration: no existing production entrypoint imports or activates the new base. Explicit setup helpers can create the store when called; no runtime startup wiring calls them in these entries. The write_txn boundary change is behind an opt-in mode.
- A spawn-inventory entry is retained for the new sources.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes Kanban storage custody and the upstream sync gate inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
