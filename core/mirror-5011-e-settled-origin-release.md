# Remote completion release and retained origin obligations

Mirror entries: 4769, 4791, 4792, 4793, 4825.

## Behavior

Previously attested completions do not reopen merely because their remote owner stops sending heartbeats. Missing replica links do not erase adopted origin obligations. Removed-owner inference is reversible when that owner publishes again.

## How it works

A publication at or before the owner last cleanly observed its scope can retain its release through silence, while transport freshness remains required. Origin projection treats verified unchanged completion evidence as settled; newer owner data can reopen it. When multiple copies share a link, any open copy keeps the link open. A qualified remembered done/archived row may be inferred released only from a fresh nonempty replica with no records or live scope heartbeat for its whole owner. The owner_removed marker is reevaluated on each fresh read, not treated as a receipt. Origin-obligation selection adds missing qualified custody without upgrading terminal evidence and marks partial uncertainty when a per-chat replica result survives an unreadable ledger.

## Code

- `hermes_cli/kanban_annotation_narrow.py`
- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_origin_ledger.py`
- `hermes_cli/kanban_origin_obligations.py`
- `hermes_cli/kanban_replica_aggregates.py`
- `hermes_cli/kanban_replica_projection.py`
- `hermes_cli/kanban_scope_heartbeat.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_activity_records.py`
- `hermes_cli/kanban_session_activity_release.py`
- `hermes_cli/kanban_session_activity_revalidate.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Release-survives-silence/offline and removed-owner suites cover newer records, stale pulls, unverified completions, open duplicate copies, returning publications and heartbeats, and warm/cold durable snapshots. Replica-origin-obligation tests cover adopted links, count/list parity, aliases, exclusions, stale uncertainty and unreadable-ledger fallback.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/hermes_cli/test_kanban_origin_alias_release.py`
- `tests/hermes_cli/test_kanban_origin_evidence.py`
- `tests/hermes_cli/test_kanban_release_removed_owner_rows.py`
- `tests/hermes_cli/test_kanban_release_survives_owner_offline.py`
- `tests/hermes_cli/test_kanban_release_survives_owner_silence.py`
- `tests/hermes_cli/test_kanban_replica_origin_obligations.py`
- `tests/hermes_cli/test_kanban_replica_read_memo_heartbeat.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

One missing record is not whole-owner removal. Stale or empty replicas prove no new absence. Stale reads neither make nor revoke an earlier removed-owner inference. An unreadable ledger cannot become a successful empty all-chat count or release unverified custody.
