# Lanes flow wait kinds, park semantics, triage attention and lane samples

Patch coverage: 6658, 6659, 6684, 6685, 6686, 6687, 6721, 6736, 6737, 6738, 6739, 6740, 6741, 6742, 6743, 6749, 6752, 6753, 6811, 6812, 6963, 6964.

## Behavior

**Lanes-flow `wait` (6658, 6721).** `kanban_lanes_flow.snapshot` adds `wait` to each card: `awaiting.kind` when the card carries an awaiting dict, else `None`. `_awaiting` (only for `status == scheduled`) takes the newest of a `scheduled` event or a `continued` event whose payload `status` is `scheduled`; a continued-to-ready event is not a park.

**Replica unfreezing (6658, 6659).** `kanban_stop_sync.frozen_change` returns False for a replica (`is_replica`: the db file's parent directory is `sync-shadow`), so only a gateway's live file freezes stopped cards. Existing replicas that already froze a card are repaired once in `kanban_sync_client._pulled_replica`: `kanban_sync_replica_unfreeze.needed` is true when no `<db>.stop-unfrozen` marker exists and either a `<db>.stop-unfrozen.pending` marker exists or the replica holds an `administrative_stop_restricted` event. For a non-diagnostic pull it writes the pending marker, raises `Resync`, and goes through the existing retention rebuild (quarantine save, descriptor invalidation, snapshot resync), so consumed link deletions are recovered; only after the fresh snapshot does `finish` write the done marker and delete the pending one. A failed rebuild retries because the pending marker survives.

**Park semantics (6684-6687).** Read-time only. `kanban_wait_info.park_semantics` adds to `describe` of scheduled / `scheduled:probe` rows the keys `prerequisite` (the parker's own `awaiting.why`, never an inferred guess, capped at `PREREQUISITE_MAX = 500`), `watching` (up to 8 card ids found in the wake check), `next_actor`, and `backstop_at`. `kanban_actionability._name_reason` (called from `project` unless `frozen=True`) rewrites the blocker prerequisite and instruction to `Waiting on: <why>. Next check: <trigger>` (for `satisfy_dependency` it prefixes `<why>; parents: ...`, and may append a clock backstop). A clock park with no recorded or only inferred reason reads `waiting reason unavailable`. `kanban_actionability_creation.intent_for` projects with `frozen=True`, so stored creation intents keep their trigger-only text. The frozen final source uses `awaited['why'][:80]` for the clock-label suffix; the separate structured prerequisite retains its 500-character bound. Patch 6684 briefly lengthened the suffix to 200, but that is not the final snapshot behavior.

**Dependency waits (6753).** `kanban_wait_info.describe(status == 'todo')` delegates to `kanban_dependency_wait_clock.describe`, which yields `waiting_on: card <parents>` and `waiting_since` only from a reader-verified `dependency_wait` payload. `kanban_session_wait_rows._local_wait_rows` now includes `todo` and calls `dependency_wait_clock.overlay`, which keeps a todo card only if `kb.parent_states` shows at least one parent that blocks it, and dates the episode from a replay of `created/linked/unlinked/dependency_wait/status/landing/claim_rejected(parents_not_done)` events; creation alone and history that does not match the current parents leave the clock undated. A todo card with no blocking parent produces no wait.

**Overdue triage attention (6736, 6737, 6739, 6740, 6811, 6963).** New `kanban_triage_attention` derives the time a card entered its current consecutive triage episode from lifecycle events (`landing(kind, payload)` maps each kind to the status it left the card in, including every registered triage writer, payload-recorded landings, legacy `continuation_exhausted`/`goal_unachievable` re-kinds and imported parked cards; 6739). A card is *overdue* when it has been in triage for more than 600 s with a known entry; there is no fallback to creation/update time, and an invalid lifecycle clock or stale/partial source leaves entry unknown. 6963 orders events by timestamp, then consistent UUIDv7 milliseconds, then local id, so rehomed cards with imported older history are not left amber; `annotate` issues one `SELECT *` per call only when a triage card exists and treats a missing table/column as unknown (6740). Hooks: `hermes_state_outcome._read_card_items` sets card state `triage_overdue`; `hermes_state_session_status.reduce_status` then sets stripe `stuck` with reason `triage_overdue` and the cards in `on`, but not when the verdict is `muted` and not when an ask exists (6739). `tui_gateway/notice_triage_clock` and `notice_fanout` keep a per-database cache of detached triage clocks, schedule a fanout at the next 600 s crossing and re-ack once per episode; remote replicas project the same clock. Desktop (`session-status-dot.tsx`, `session-row-card-squares.tsx`, `session-outcome.ts`, a locale key, and in 6738 an overlay-gap entry) render the overdue label and counts. 6811 removed the retained-stop exemption so a stopped retained card also ages red. (I read the fanout integration at the level of `notice_triage_clock`, not line by line.)

**Explicit retained stops (6752).** New `kanban_retained_status.annotate` (called from `triage_attention.annotate`) marks a triage card `retained_stop` only when it has no run, claim, human request or running run and its newest relevant event is `decision_retained` with `disposition.reason == 'scope_stopped'`; any later claim, schedule, continue, block, review or park clears it. The outcome and status reducers carry it to Desktop as retained/incomplete work distinct from an execution wait. As of 6811 it no longer suppresses overdue.

**Lane samples (6741-6743).** `kanban_lane_samples.observe` wraps a real (non-dry-run, non-shadow) dispatcher pass: it takes a lane occupancy census, installs a `Capture` for lanes, and in a `finally` records minute samples (`lane_samples`, 8-day retention) via a savepoint upsert. Cap rejections from `lane_workers.drop_full` (pass level) and `lane_workers.at_capacity` (claim-time recheck, 6742) are captured with the highest occupancy seen per lane. Capture and record failures are logged and never change admission; if savepoint cleanup fails with a live transaction, the connection is rolled back so the next dispatcher writer is not poisoned (6743). Advisory only.

**Test-only or minor.** 6749 makes the review e2e clock honor a provider reset; 6964 and 6812 adjust fixtures and pins; 6737 also changes `sessions_projection_manifest.py` (declares the new triage-attention fields) and `notice_fanout_result.py`.

## How it works

Entry points: `lanes_flow.snapshot`, `_pulled_replica`, `wait_info.describe`, `_local_wait_rows`, `_read_card_items`/`reduce_status`, `notice_fanout`, and the dispatcher pass wrapped by `lane_samples.observe`.

## Code

`hermes_cli/kanban_lanes_flow.py`, `kanban_stop_sync.py`, `kanban_sync_client.py`, `kanban_sync_replica_unfreeze.py`, `kanban_actionability.py`, `kanban_actionability_creation.py`, `kanban_wait_info.py`, `kanban_dependency_wait_clock.py`, `kanban_session_wait_rows.py`, `kanban_triage_attention.py`, `kanban_retained_status.py`, `kanban_lane_samples.py`, `kanban_lane_workers.py`, `kanban_candidate_receipts.py`, `hermes_state_outcome.py`, `hermes_state_session_status.py`, `hermes_state_session_status_snapshot.py`, several session-activity/origin-ledger/replica-projection readers, `tui_gateway/notice_fanout.py`, `notice_fanout_result.py`, `notice_triage_clock.py`, `kanban_unloaded_gates.py`, and Desktop files under `apps/desktop/src/{app/chat,lib,i18n,types}` plus `locales/_keys.desktop.json`.

## Tests

Read from source; **not executed here**; no fresh run and no native or end-to-end green claim. Python: `test_kanban_lanes_flow(_evidence).py`, `test_kanban_stop_sync.py`, `test_kanban_actionability_contract.py`, `..._wait_reason.py`, `test_kanban_cli_outside_park_live_claim.py`, `test_kanban_dependency_wait_clock.py`, `test_kanban_triage_attention.py`, `test_kanban_triage_import_order.py`, `test_kanban_retained_status.py`, `test_kanban_origin_ledger.py`, `test_kanban_lane_samples.py`, `test_kanban_lane_samples_transactions.py`, `tests/hermes_state/test_session_status.py`, `tests/tui_gateway/test_triage_attention.py`, `tests/e2e/core/kanban/test_kanban_rate_limit_review.py`. Desktop vitest files `session-status-dot.test.tsx`, `session-row-card-squares.test.tsx`, `session-row-stripe.test.tsx`, `session-outcome.test.ts` are changed in the diffs, not run. Real dashboards and live boards were not exercised.

## Applying

Apply in ordinal order; 6736-6740 are one change set with review corrections, and 6811/6963 change the shared `overdue`/`current_entry` again.

## Gotchas

- Projection is read-time only; frozen creation intents are never rewritten.
- Mute and human ask take precedence over overdue triage.
- Overdue triage is attention, never an ask, release or claim.
- A replica rebuild discards the file; the pending marker is what makes it retry.

## Why core, not a plugin?

Kanban status reducers, dispatcher and Desktop row rendering.
