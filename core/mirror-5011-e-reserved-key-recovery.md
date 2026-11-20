# Audited recovery of never-created reserved-key cards

Mirror entries: 4773, 4774, 4806.

## Behavior

An operator can preview a narrowly scoped correction of an ordinary card accidentally given a detector-reserved key, then apply it only against the exact preview. Ordinary creation now rejects reserved detector keys before lookup or writes.

## How it works

The reserved-key sync command requires a replacement key and reason; apply additionally requires the preview token. Authenticated never-existed evidence, home identity, absence of active workers/leases, local compare-and-swap, uniqueness and immutable supersession receipts constrain the repair. Corrected parent intent reaches the Board before original child identities, retaining rejected payloads and audit history. Initial multi-page evidence checks enforce tasks-table identity. Moving snapshots are later supported by covering the finite first-to-last head interval with contiguous authenticated full-row changes; any transient key match, gap, compaction, failure or malformed feed refuses. Evidence remains subject to its original 30-second bound.

## Code

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_creation.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_pickup_alarm_identity.py`
- `hermes_cli/kanban_sync_cli.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_compat.py`
- `hermes_cli/kanban_sync_key_evidence.py`
- `hermes_cli/kanban_sync_rejections.py`
- `hermes_cli/kanban_sync_reserved_key.py`
- `website/docs/user-guide/features/kanban-reserved-key-recovery.md`
- `website/sidebars.ts`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Reserved-key primary, boundary, safety, extra and snapshot suites cover read-only previews, race vetoes, exact receipts, resumable delivery, rollback, byte retention and rejection of compatibility downgrades. Key-evidence tests cover behind-cursor insert/update, transient duplicates, moving writers and finite-cut completion, gapped or missing feeds and evidence expiry.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/hermes_cli/test_kanban_lanes.py`
- `tests/hermes_cli/test_kanban_priority_audit.py`
- `tests/hermes_cli/test_kanban_sync_key_evidence.py`
- `tests/hermes_cli/test_kanban_sync_reserved_key.py`
- `tests/hermes_cli/test_kanban_sync_reserved_key_boundaries.py`
- `tests/hermes_cli/test_kanban_sync_reserved_key_extra.py`
- `tests/hermes_cli/test_kanban_sync_reserved_key_extra2.py`
- `tests/hermes_cli/test_kanban_sync_reserved_key_safety.py`
- `tests/hermes_cli/test_kanban_sync_reserved_key_snapshot.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

This is observational key absence at the final cut, not an atomic remote reservation. It cannot repair detector-owned, already acknowledged, foreign, active or insufficiently evidenced cards. It never synthesizes another link endpoint or clears rejection history wholesale. No production repair is asserted here.
