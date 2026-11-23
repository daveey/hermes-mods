# Kanban sync first-send envelopes and frozen replay (mirror 5510–5518)

## Behavior
- Outbox wire envelopes are persisted before network I/O, so an uncertain send (lost response, restart) is retried byte-identically with the same mutation identity and clock instead of rebuilding payloads with a fresh HLC that could overwrite newer edits after receipt expiry. Synthetic parent identity is preserved across uncertainty; ack/prune cleans pending records. Corrupt/unknown envelopes are not silently rebuilt and legacy attempted rows are not migrated.
- Definitively refused parents (permanent refusals such as live_claim/invalid synthetic parents) retire their envelope without growing rejected-parent rows or diagnostic children; only schema/transient refusals materialize retry rows. Pending task retries suppress duplicate parent synthesis even while in backoff.
- Capture origins are bound (explicit `frozen_replay`/`status_origin` capability and a valid serving HLC required by the worker-first protocol), serving clocks are absorbed before freezing, definitive-repair provenance and current-snapshot status coherence are preserved. Capture triggers are installed only after checking the actual cursor/schema; old identity provenance is revoked before ID mutation; the standalone sync schema does not depend on `tasks`.
- A materialized parent queued after dependents is promoted ahead of them, and dependents are held through backoff/quarantine and pre-head filtering; limits apply after filtering so unrelated cards progress. Selection logic lives in `kanban_sync_parent_order.py`, not the client facade.

## How it works
`kanban_sync_envelope.py` (persistence), `kanban_sync_compat.py`, `kanban_sync_client.py`, `kanban_db_sync.py`, `kanban_sync_status_origin.py`, `kanban_sync_parent_order.py`, `kanban_sync_quarantine.py`, `kanban_sync_hub_routes.py`, `kanban_db_connect.py`. Docs in source: `docs/kanban-first-send-envelopes.md`, `docs/designs/kanban-frozen-replay.md`.

## Code
All under `hermes_cli/`.

## Tests
By test names: `test_kanban_sync_envelope.py` (lost response freezes payload and clock before network I/O and across restart; synthesized parent identity survives a lost reply; release retries keep the whole envelope; a containment rekey does not replay a retired envelope), `_envelope_edges.py` (commit before I/O and ack cleanup; delete and run retries across reopen; parent ack and pruning; pending parent owns backoff), `_envelope_rejection_recovery.py` (definitive parent refusal retires the identity), `test_kanban_sync_forbidden_field.py` (rejected synthetic parent enters the bounded path; no accumulation across rounds), `test_kanban_sync_clock_capability.py`, `_origin_capability.py`, `_origin_repairs.py` and `test_kanban_sync_status_origin.py` (remote clock merged before freezing; no capability keeps legacy rebuild; repair keeps the captured origin; trigger detach/reattach and identity replacement revoke captured origins; rollback, savepoints and multiple status intents), `test_kanban_sync_parent_order.py` (backoff prefix does not hide an independent card; pre-head filter cannot reintroduce a missing parent; promotion keeps frozen bytes), `test_sync_parent_review3.py` / `review5.py` (parent recovery after a permanent refusal lifts; children during parent backoff; child ahead of its parent beyond the batch; one transient refusal then a healthy board), and `test_kanban_sync_links_replica_revision.py` (5511: bootstrap WAL settled in the fixture). The `test_p3_frozen_wake_after_completion_arrives` probe prints batches and asserts only that a status still sent after a completion is origin-bound and marked `frozen_replay`. Not executed in this mirror pass.

## Applying
Apply in order 5510→5518; later patches correct earlier ones.

## Gotchas
Sender-side only: server-side replay arbitration is out of scope. The `test_p3_frozen_wake_after_completion_arrives` probe in the source exercises a frozen retry after a completion event arrives; whether the retry's wake status can be stale is not settled by anything the diff states, so treat that case as unproven.

## plugin?
No.
