# Live snapshot sweeps and replica-age regression contracts

Mirror ordinals: 3731, 3750, 3764, 3782.

## Behavior

- After a retention-floor 410, rebuilding a primary board's live file removes owned rows absent from a completely read snapshot rather than retaining Board deletions forever.
- Unsynced local work, local tombstones, other gateways' cards, recent writes and historical events outside the import window are protected.
- Replica-age tests pin restart continuity, future observation rejection and disk-only detach cleanup.

## How it works

`kanban_sync_client` calls `kanban_sync_resync_sweep.resync_live` for the live-file rebuild. A Hub proxy records returned UIDs and only marks tables complete after the last snapshot page. Candidate SQL limits deletion to this gateway's partition, excludes row/card outbox holds and tombstones, checks the rebuild cutoff and preserves old events whose absence merely reflects retention. Synthetic feed deletes are applied children-first through the normal handlers and observers. `apply_remote_changes(keep=...)` rechecks candidates inside the apply transaction without advancing the existing cursor.

The replica-age patch is test-only: it exercises the existing persisted observation clock, including a record present only on disk after restart and repeated restarts through a sustained stall.

## Code

- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_resync_sweep.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/hermes_cli/test_kanban_sync_live_resync_sweep.py` — A real sync round crosses the retention floor and removes deleted owned cards/children while preserving local pending work and foreign copies. Separate cases assert incomplete tables do not sweep, post-cutoff rows survive without relying on an outbox hold, a racing outbox insertion wins the in-transaction recheck, old events remain, pending/rejected card outboxes protect children, new child timestamps survive, and children receive their own tombstones before card cascade.
- `tests/hermes_cli/test_kanban_sync_replica_age.py` — Detach removes a memo-only clock; a future same-boot seen_at starts afresh, a valid earlier observation resumes, and repeated restarts preserve one long-stall clock/card while refreshing seen_at to the latest observation.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

Snapshot absence is deletion evidence only for a completed table and eligible owned rows. The cutoff uses whole seconds with outbox protection for same-second local writes. This documents this batch's sweep contract, not later refinements elsewhere in the mirror.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.
