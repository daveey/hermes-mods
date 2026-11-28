# Truthful non-running reasons and refusal provenance

Patch coverage: 6600, 6601, 6602, 6603, 6611, 6672, 6796, 6797, 6798, 6808, 6809.

## Behavior

All of this is live in the dispatcher tick, replica/owner readers, `describe`, `kanban why` and Desktop. It is **display evidence only**: nothing here changes admission, release, retry or lifecycle. A non-running card (status `ready`, `todo`, `scheduled`, `blocked`, `triage`, `review`, no current run) can carry a `non_running_reason` with a fixed-vocabulary `code/family/disposition/actor/summary/next_action`, a freshness and its own clock; anything else is `unknown` with an `unknown_reason`.

**1. Producing it (6600).**
- `dispatch_once` calls `kanban_capacity_evidence.capture(conn)` before the pass (non-dry-run): up to `TASK_LIMIT` non-terminal task rows plus each card's newest non-`dispatch_skipped` event id, inside a savepoint. After the pass `observe_dispatch` first runs `kanban_dispatch_skip.record_tick(..., captured, machine)` and then `record(...)`, so the pass's own skip events do not invalidate its snapshot.
- `from_result`/`describe` in `kanban_nonrunning_reason` map real producer tokens (claim-deferral codes, skip buckets, quiet deferral, update drain, memory pressure, at-capacity) to a reason. Unknown codes become `unclassified_refusal` / `unknown`; `offline`/`denied`/`lease_held` count as a Board lease refusal only when the detail carries `origin == 'board'`. Capacity, resources, storage, drain, placement, contention, `start_gate` and `skipped_nonspawnable` reasons are scoped `observing_dispatcher` (summary prefixed "Observing dispatcher:"); a blocked or unknown task-specific reason beats a global capacity cap.
- `_qualified_reason` emits it only if the card's row and causal event are unchanged since capture. `record` stores it in the `kanban_capacity_observation` snapshot (180 s validity, `decision_at_ms`) and refuses to overwrite a newer observation with a late one.
- Gateway/worker admission now records `gateway_draining`, `update_admission_hold`, `worker_draining`, `update_admission_busy`, `update_admission_unavailable` and `turn_admission_waiting` as claim deferrals, and `kanban_governors._claim_charge` names `usage_accounting_pending`, `decision_pending`, `spend_policy_unavailable`, `spend_budget_exhausted` explicitly and labels an unknown local deferral `unrecorded` instead of `board_lease`.
- Published form (`kanban_nonrunning_transport.envelope`, appended as `non_running_v1` to the `dispatch_skipped` event): observer machine, board, task id, a SHA-256 binding of the task's synced columns plus the newest non-skip event uid, `observed_at` and `valid_until = observed_at + 180`. Only `ready` cards with no current run.
- `wait_info.describe` gives a `gave_up` triage with payload `trigger_outcome == 'spawn_failed'` the startup-failure wording and `reason_code`.
- Two new `task_events` indexes (`(task_id, id)` and a partial causal one excluding `dispatch_skipped`) are created in schema and migration.

**2. Reading it.**
- Local: `kanban_execution_source.assemble` adds the dispatch snapshot and `remote_non_running_reason` and bounds event/run history with indexed per-task `LIMIT`s plus an `event_watermark`. `enrich` (called from `raw`) keeps the phase and clocks untouched; for non-`ready` statuses it sets `unknown/lifecycle_reason_unobserved`; for `ready` it accepts the local snapshot only when machine, board, status, run, assignee, `runs_on`, the event watermark and the 180 s window all match, else `unknown` with the matching reason code.
- Remote (`kanban_nonrunning_transport._read`): per card, the newest 16 `dispatch_skipped` events whose feed-stamped `origin_gateway` equals the payload's observer, binding equals the current binding, and board/task match are candidates. Corrupt or over-long history, or more than 16 events, marks the card unknown (omitted history may hold a newer decision). Newer expired/unknown decisions are kept as tombstones so they cannot resurrect an older refusal. 6602 restores the typed `ClaimDeferral(detail, origin)` after qualification so board-origin lease refusals survive JSON. Same-observer contradictory decisions at the same clock (6603, `_observer_reason`) and contradictory observers become `dispatcher_evidence_conflict`; if a remote conflict exists, `enrich` returns it even when a local current reason exists (6602). If local and remote are both current and differ, also conflict; if local is not current the remote one is used.
- Replica path: `kanban_chat_links._read_replica` calls `replica_rows` inside one read transaction (the row's status, `created_on` and assignee must still match the replica), then `replica_fields` after owner qualification; `kanban_origin_ledger.project` adds `kanban_nonrunning_owner.fields` for authenticated owner records (`select`: scope components must match, a fresh running/done/failed phase for the same card makes it a conflict, undated evidence is order-unknown).
- Python `execution_observations.nonrunning_reason` and Desktop `execution-observations.ts nonRunningReason` expire a cached nested reason independently of the phase: only a `current` blocked/waiting reason with all text/ref fields and `now < min(valid_until, observed_at+180)` survives; otherwise `unknown`/`dispatcher_evidence_stale`.
- 6611: `describe` returns plain `str` codes (not `ClaimDeferral` subclass instances) after classification.

**3. Archived diagnostic withdrawal (6796, 6797).** `kanban_sync_actionability_drift.audited_rejection` now treats an `archived` status or event like done history and, if either copy is archived, calls `kanban_sync_actionability_archive.superseded` (else the existing terminal proof). That proof is read-only and returns true only when live and shadow agree on: archived task with no lease/idle fields set and not deleted, exactly one original source event before the final archive event, a valid `_recovery_history_v1` predecessor graph (unique identities, strictly earlier predecessors, the archive as the sole head), a recognized `source_status`, and a complete closed run inventory (non-running, outcome set, ordered times, unique uids, not deleted). 6797 makes an explicitly `null` `_recovery_history_v1` fail closed instead of being skipped. Archive is withdrawal, not completion, delivery or permission to retire or replay.

**4. Canonical `why` (6808).** `kanban_governors.card` returns None when the recorded governor snapshot's status differs from the task's current status or the task is missing.

**Test-only:** 6601 retains typed admission wait diagnostics in fixtures; 6672 compares persisted clocks as millisecond units; 6798 and 6809 are test arrangement/parity.

## How it works

Entry points: `kanban_db_dispatch.dispatch_once` → `capture` / `observe_dispatch` / `record_tick`; readers: `kanban_execution_source.assemble`/`raw`, `kanban_chat_links._read_replica`, `kanban_origin_ledger.project`, Desktop `projectExecutionObservation`; archive: `audited_rejection`.

## Code

`hermes_cli/kanban_nonrunning_reason.py`, `kanban_nonrunning_transport.py`, `kanban_nonrunning_owner.py` (new), `kanban_capacity_evidence.py`, `kanban_db_dispatch.py`, `kanban_dispatch_skip.py`, `kanban_admission.py`, `kanban_gateway_drain.py`, `kanban_governors.py`, `kanban_execution_source.py`, `kanban_execution_observations.py`, `kanban_chat_links.py`, `kanban_origin_ledger.py`, `kanban_wait_info.py`, `kanban_db.py`, `kanban_db_connect.py`, `execution_observations.py`, `kanban_sync_actionability_archive.py` (new), `kanban_sync_actionability_drift.py`, `apps/desktop/src/lib/execution-observations.ts`.

## Tests

Read from source diffs; **not executed here**; no fresh run and no native or end-to-end green claim. `tests/hermes_cli/test_kanban_nonrunning_reason.py`, `..._reason_boundaries.py`, `..._selection.py`, `..._review.py`, `..._transport.py`, `..._lifecycle_wait.py`, `..._producer_contracts.py`, `test_execution_observations.py`, `test_kanban_sync_actionability_archive.py`, `test_kanban_why_fast.py`, `test_kanban_hub_admission_turns.py`, `tests/gateway/fixtures/kanban_recovery_probe.py`, `apps/desktop/src/lib/execution-observations.test.ts`. Mutant/clause-kill counts in commit text are owner-reported. A real multi-gateway deployment and the archive proof on production data are **untested** here (the commit text mentions only an offline export).

## Applying

Apply in ordinal order. The new indexes are created by `CREATE INDEX IF NOT EXISTS` on first connection; check DB size/time on a large board.

## Gotchas

- Unknown is a first-class outcome; do not fill in a reason for display.
- A reason expires after at most 180 s and is never renewed by readers or collector heartbeats.
- Conflict must be detected before generic unknown reduction.
- Read-time projection only; frozen creation intents and lifecycle authority are untouched.

## Why core, not a plugin?

Kanban dispatch/sync internals and the Desktop renderer are core.
