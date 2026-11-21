# Certified per-card containment import during pickup

Mirror entries: 5112.

## Behavior

- Worker pickup re-certifies protected event receipts from its replica into the live receipt cache before importing the card.
- A card whose evidence cannot be certified is visibly skipped without preventing other cards in the batch from importing.
- Existing containment guard triggers remain enabled and reject uncertified history/edges.

## How it works

`adopt_receipts()` reads each protected event's cached replica evidence, uses `remember()` to validate board/origin/signature and stop/release pairing, then requires the event to equal its receipt. This is an offline replica-to-live path, not a network bypass.

`import_cards()` performs certification and application per card. Refusals are logged with card/table and recorded in `kanban_pickup_skips` with reason `other`, instead of rolling back the entire mixed batch. Successful cards keep the normal import/claim path.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_cli/kanban_containment_replica.py`
- `hermes_cli/kanban_worker_node.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

`test_kanban_pickup_containment_import.py` uses real containment triggers to import/claim a released card, skip an uncertifiable card while importing a plain one, and prove direct uncertified event/edge writes still refuse. Live worker pickup, remote receipt fetching and fleet behavior are untested here.

Source test/coverage paths:

- `tests/hermes_cli/test_kanban_pickup_containment_import.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

Trust comes from re-certifying stored evidence, not from trusting a replica row blindly. A skip is visible failure for that card, not successful custody. Do not disable triggers or manufacture a receipt to make a batch progress.

## Why core instead of a plugin

Pickup import and the live canonical receipt cache share core database guard invariants; a plugin after import is too late to satisfy protected-event admission atomically.
