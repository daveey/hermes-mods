# Forwarded-card wait survives replica lag

Patch 1373; source `5dc666b7b97cae2715237edffef3f13a3e0fdfbf`.

## Behavior

A just-forwarded card absent from its replica remains a card dependency rather than becoming a clock-only retry. Archived or otherwise invalid producers are not silently accepted.

## How it works

Confirmation retries twice with a three-second delay. INVALID_AWAITING then permits an unconfirmed card park using the target-provided ID and the existing wake probe. Other failure codes propagate; fallback messages include exception text.

## Code

hermes_cli/kanban_placement_forward.py

## Tests

Source regression inventory: tests/hermes_cli/test_kanban_placement.py::test_just_forwarded_card_the_replica_has_not_synced_parks_on_the_card_not_the_clock; test_archived_producer_is_not_parked_on.

These runtime suites were not rerun in this mirror pass. Executed mirror inventory/replay/tree checks are recorded in [mirror-1375-verification.md](mirror-1375-verification.md); they do not certify runtime behavior.

## Applying

Apply the complete snapshot using the [README replay recipe](README.md#applying), not this patch independently.

## Gotchas

Unconfirmed is not delivery proof; the wake probe still rereads the target. Existing producer-done settlement remains bounded.

## Plugin?

This patch mirrors the source implementation; no separate public mod installation is required.
