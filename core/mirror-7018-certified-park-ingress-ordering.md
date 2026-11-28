# Certified park ingress ordering in Kanban sync push

Patch coverage: 6630, 6631, 6632.

## Behavior

When a card is parked with an owner-reassessment receipt, the pending status upsert and its park event are pushed in a fixed order: the exact pending status origin ("parent") goes first, bound to the park event it carries. Certified ordering applies only when the Board capability advertises `owner_reassessment_v1` as the exact integer 1 (bool/None/other values are unsupported); the bound park event is then exempted from the evidence-first promotion because the Board inserts and seals it atomically from the status op. A frozen certificate (its wire fields already carry the receipt) is never sent without that capability: it, and the park that depends on it, are held rather than falling back to ordinary order, and they are also held when the certificate is not yet sendable, backing off, or outside the batch's allowed set. A lone `owner_reassessment_v1` field is never trusted; a missing journal or malformed fields (when no frozen certificate exists) fall back to the ordinary order.

## How it works

`hermes_cli/kanban_owner_reassessment_transport.py` adds `pending_park_parent` (finds the one pending `tasks` upsert whose `status_origin` is the park event uid, validated by journal) and `bound_park_uid` (requires status `scheduled`, matching `status_origin`/`evidence_uid`, and a saved receipt equal to the wire receipt). `hermes_cli/kanban_sync_parent_order.py` (`reassessment_supported`, `select_pending`, `parent_first`) uses them; `kanban_sync_evidence_order.py` adjusts event-order helper (`firsts`). In `kanban_sync_client.push` the status-origin capability is now read once per batch *before* selection so selection and serialization agree. Commit 6632 only reuses a lifecycle fixture without imported-name shadowing.

## Code

`hermes_cli/kanban_owner_reassessment_transport.py`, `kanban_sync_parent_order.py`, `kanban_sync_evidence_order.py`, `kanban_sync_client.py`.

## Tests

Read from source; **not executed**. `tests/hermes_cli/test_kanban_owner_reassessment_transport.py`, `test_kanban_owner_reassessment_mutation_gaps.py` (guard-boundary pins for each clause). Push entry paths other than the batch selection are **untested**.

## Applying

Apply 6630-6632 in order with the series.

## Gotchas

- Capability snapshot must be taken once per batch.
- Never search "latest task row"; only the exact pending origin qualifies.

## Why core, not a plugin?

Sync client internals.
