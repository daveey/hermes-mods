# Disposable certificates for empty creator-event backfill

Mirror entries: 4826.

## Behavior

Backfill avoids repeatedly decoding creator events already proven empty by the existing Python semantics, without changing unresolved truthy creators or inheritance order.

## How it works

Only a successfully decoded falsy creator receives an event-ID certificate. Insert/update/delete triggers invalidate affected identities; both the read snapshot and writer transaction verify trigger coverage, clearing certificates after coverage gaps. Recovery salvage and backup fill discard this disposable data. A nonflattened co-routine envelope filters outside the original query. Per-call query-plan checks require original traversal parity and an exact outer primary-key probe; unknown plans fall back to literal/Python filtering. Transaction-loss errors propagate instead of permitting later accidental autocommit.

## Code

- `hermes_cli/kanban_creator_chats.py`
- `hermes_cli/kanban_creator_empty.py`
- `hermes_cli/kanban_db_recover.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Backfill tests cover Python falsy versus invalid/truthy values, mutation invalidation, reordered and below-cursor IDs, trigger/schema gaps, concurrent snapshots, rollback, serious SQLite errors, query-plan fallbacks and same-pass inheritance. Recovery tests cover real rebuilds and stale backup certificates; sync integration retains local-clobber coverage.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/hermes_cli/test_kanban_creator_empty_recovery.py`
- `tests/hermes_cli/test_kanban_sync_local_integration.py`
- `tests/tools/test_kanban_backfill_empty_events.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

Certificates are an optimization, never authoritative creator resolution or backup state. No sweep, attachment, repair or reconciliation interval is lengthened. Synthetic warm-backfill evidence is not whole-loop CPU acceptance.
