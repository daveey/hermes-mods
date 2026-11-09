# Replica idempotency-key families in overlap checks

Patch **2061**. Follow-up to [create overlap checks](kanban-create-overlap.md).

## Behavior

Overlap checks can apply the existing same-key-family exemption to cards published by another gateway, rather than only local cards. Missing or non-string remote keys retain conservative overlap refusal. Singleton jobs retain their stricter rule even for the same family.

## How it works

`_replica_cards` reads a string `idempotency_key` from each published task and passes it through `key_family`; otherwise it passes None. The normalized replica record previously always had `key_family: None`. The existing overlap decision consumes that field without changing its singleton precedence.

## Code

`hermes_cli/kanban_overlap.py::_replica_cards`.

## Tests

**Not rerun this pass.** `tests/tools/test_kanban_create_overlap_replica_family.py` exercises `_replica_cards` and `kanban_overlap.check` against an injected SQLite replica. Cases cover published family decoding, malformed keys, same-family remote admission, unpublished/other-family refusal, an unkeyed new card and same-family singleton refusal. These are overlap decision entry paths, not full tool/CLI/REST creation integration tests.

## Applying

Apply 2061 within [the core replay](README.md). Remote publishers must actually include `idempotency_key` (the source patch names client 1.9.27 support) before this can change their overlap decisions.

## Gotchas

Absent publication is not evidence of a different family and does not earn an exemption. This is family-based overlap handling, not a cross-gateway exactly-once creation guarantee. It does not bypass singleton work ownership.

## Plugin eligibility

Not plugin-only: replica normalization feeds the core admission overlap guard shared by creators.
