# Tenant-bearing producer cards through worker-pool placement

Source patch ordinals: 5784–5785.

## Behavior

Worker-pool placement can carry the existing Board-row `tenant` option, including cards produced by spend/review entry points. The legacy create-task courier still does not carry it.

## How it works

`POOL_CARRIED_OPTIONS` includes `tenant` alongside `pin_mandatory`. `tenant` deliberately remains in the legacy `UNCARRIED_OPTIONS` set: synced Board transport, not a local host resource, carries this field to workers. Placement exclusion fixtures are aligned with this distinction.

## Code

- `hermes_cli/kanban_placement.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/hermes_cli/test_kanban_pool_tenant.py` covers spend producers, review tool/CLI producers, Board transport to a worker and the unchanged legacy courier restriction. `test_kanban_worker_placement.py` updates the exclusion fixture for carried tenants.

## Applying

Apply the placement constant and corresponding fixtures. Use the existing synced Board/worker-pool route for tenant-bearing cards.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

This does not add tenant support to the legacy courier or change tenant authorization. A field being transported is not evidence that any arbitrary tenant value is authorized.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
