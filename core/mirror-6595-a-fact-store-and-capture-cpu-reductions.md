# Fact store, reducer and capture CPU reductions

Patches: 6025, 6030, 6043, 6068, 6125, 6159.

## Behavior

- Unchanged reducer outputs are left in the immutable base instead of republished; the store does not re-encode an upsert equal to the stored fact (sizing is deferred and committed once if needed).
- Fact copies share the frozen value via an evolve helper, size computation uses the C JSON encoder, and exact scalar builtins bypass ABC dispatch.
- Board evidence reads incrementally (only changed rows re-read) and paced: a selector starts at most once per 5 s and has its own 30 s capture limit counted from its own start; repeated cold input failures keep unknown instead of stale.

## How it works

fact_reducers, fact_store, fact_contract, fact_wire and fact_board_inputs/native implement these.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/fact_annotation_membership.py`
- `hermes_cli/fact_board_inputs.py`
- `hermes_cli/fact_board_native.py`
- `hermes_cli/fact_board_startup.py`
- `hermes_cli/fact_contract.py`
- `hermes_cli/fact_expiry.py`
- `hermes_cli/fact_reducers.py`
- `hermes_cli/fact_remote_links.py`
- `hermes_cli/fact_replica.py`
- `hermes_cli/fact_source_commit.py`
- `hermes_cli/fact_store.py`
- `hermes_cli/fact_wire.py`
- `hermes_cli/sessions_native_source.py`
- `plugins/kanban/dashboard/board_fact_capture.py`
- `plugins/kanban/dashboard/board_fact_views.py`

## Tests

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/hermes_cli/test_fact_board_inputs.py`
- `tests/hermes_cli/test_fact_board_pacing.py`
- `tests/hermes_cli/test_fact_capture_scale.py`
- `tests/hermes_cli/test_fact_evolve.py`
- `tests/hermes_cli/test_fact_reducer_quiet_publication.py`
- `tests/hermes_cli/test_fact_store.py`
- `tests/plugins/test_board_native_incremental.py`
- `tests/plugins/test_board_pending_route.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Wire shapes, budgets and failure gates are retained.
- A failed publish keeps the cursor, so retries re-do the work.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes the fact subsystem inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
