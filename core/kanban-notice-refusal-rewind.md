# A refused Kanban notice is handed back, not dropped

Patches: 0132 0792 0793 1597

- `patches/0132-fix-tui-hand-a-refused-kanban-notice-back-to-the-cha.patch`: backend
  (`tui_gateway/session_notifications.py`).
- `patches/0792-fix-kanban-deliver-tui-kanban-notices-at-least-once-.patch`
- `patches/0793-test-kanban-notice-arriving-while-the-chat-compacts-.patch`
- `patches/1597-fix-kanban-a-live-process-s-never-acked-notice-claim.patch`

What you see: a chat open in two Hermes backends (Desktop plus a phone client, or two Desktop windows) still gets
every Kanban "✔ done" / "⏸ blocked" notice for the cards it made. Before, one of them could vanish: the agent never
heard that a child card finished, so a "send it when it's ready" follow-up never ran.

## Behavior

The following describes 0132's refusal path. Patches 0792, 0793 below supersede it for local notices already recorded durably: those rows are not rewound on a turn refusal.

- A chat's Kanban notices are polled by every backend that has the chat open. The poll claims the board's
  notify cursor, then starts an agent turn. When the admission gate refuses that turn (`SESSION_NOT_OWNED`: another
  backend owns the chat), the claimed cursor range is rewound (compare-and-swap) and the notice is dropped from this
  backend's buffer. The owning backend's next poll (≤5 s) claims and delivers it.
- The refused backend stops claiming for 15 s, doubling to 5 min, so it doesn't re-claim the same events every 5 s.
  An accepted turn resets the backoff.
- A turn that raises before it starts is treated as refused, too.
- If part of a claim was already delivered (diagnostic/normal split), the undelivered part stays buffered in this
  backend instead of being rewound, so the delivered part is never re-sent.
- Delivery stays exactly-once on the happy path. On refusal it becomes at-least-once via the owner, never zero.

## How it works

- Root cause (incident 2026-10-18, a child card): `_notif_poll_kanban_scoped` called
  `claim_unseen_events_for_sub` (cursor advanced in the DB), moved the texts out of `_kanban_pending`, and called
  `_notif_submit`, which ignored `_run_prompt_submit`'s `False`. agent.log showed `Refused active session …: already
  held by pid=… surface=tui` / `Refusing turn … SESSION_NOT_OWNED` 2 s after the child completed, and the parent
  chat's transcript never got the notice. The same happened to another card's notice 2 h earlier.
- `_kb_poll_board` now records each claim (`board`, sub key, `old`/`new` cursor, the texts it produced).
  `_notif_submit` returns the gate's verdict. On `False`/exception, `_kb_rewind_refused` rewinds every claim whose
  texts were all refused (newest first, `kanban_db_notify.rewind_notify_cursor`, the existing CAS helper the gateway
  notifier uses) and sets the backoff. `_kb_mark_delivered` drops delivered texts from their claims.
- Upstream main has the same loss (its kanban caller also ignores the verdict).

## Tests

- `tests/tui_gateway/test_kanban_notify_refusal.py` (real kanban DB, faked admission gate): refused completion is
  rewound and delivered by the owner; backoff blocks re-claiming, then clears on success; a completion arriving while
  the chat is busy with other notices is delivered once; refusal after a busy wait still rewinds; a reopened card
  notifies each event once; a submit exception rewinds. 5 of the 6 fail on the old code.
- Existing `test_kanban_notify_poller.py` and `test_notification_turn_release.py` pass unchanged.

## Applying

Core-only; no config. Takes effect when the backend restarts on the new core.

## Gotchas

- Patches 0792, 0793 add durable claims and transcript deduplication for local tui subscriptions; see below. Remote-only/unkeyed notices retain the previous submit path and are not covered by the new backend-replacement tests.
- A durable transcript row proves delivery to the chat, not successful completion of the agent's follow-up work. The `follow-through` plugin remains a separate check on that work.

## Durable local notices across backend replacement (0792, 0793)

Patch **0792** (fork `bf9ca96750`) keeps a busy chat's local tui notice claim in flight until the notice is recorded in the transcript. A replacement backend can recover a dead holder's claim rather than trusting the already-advanced cursor. Patch **0793** (fork `9cf6985e99`) adds the compaction regression test; it changes no production code.

### Behavior

- A notice buffered during a running turn survives the claiming process dying: the next poller can reclaim and submit it.
- The live poller writes a `kanban_notice` row before submitting the notification turn, stages that row for adoption rather than a second user-row write, then acknowledges the board claim.
- Redelivery after a crash between row write and acknowledgment finds the existing event keys and neither writes another row nor submits another turn.
- A busy transcript or compaction-closed parent retains the buffered notice and in-flight claim for a later poll. After compaction publishes a child session, the notice lands once in that child, not the closed parent.
- The unloaded sweep can recover an orphaned local claim and shares event-key identity with the live poller.

### How it works

`kanban_notify_subs` gains `inflight_from`, `inflight_pid`, and `inflight_start`. Durable cursor claims record their holder; `recover_orphaned_notify_claims` checks process identity and rewinds dead holders. A live holder blocks another durable claimant. Acknowledging a later range does not clear an earlier outstanding range.

`_kb_persist_batch` records the notice before submit. `SessionDB.record_kanban_notice` deduplicates stable `kanban_event_keys` across the compression lineage inside its writer transaction. `_kb_ack_durable` acknowledges contiguous durable claims; `_kb_release_claims` hands undelivered ranges back when the poller stops. A refused turn does not rewind notices already durably recorded. The unloaded sweep stamps the same event keys.

### Code

`hermes_cli/kanban_db.py`, `hermes_cli/kanban_db_connect.py`, `hermes_cli/kanban_db_notify.py`, `hermes_state_messages.py`, `tui_gateway/session_notifications.py`, and `tui_gateway/kanban_unloaded.py`.

### Tests

`tests/tui_gateway/test_kanban_notice_at_least_once.py`:

- `test_notice_claimed_while_busy_survives_backend_replacement`
- `test_notice_row_is_written_before_the_turn_and_the_claim_acked`
- `test_redelivery_after_a_crash_between_write_and_ack_does_not_duplicate`
- `test_busy_transcript_keeps_the_claim_in_flight_and_buffered`
- `test_live_holder_blocks_other_claimers_and_is_not_recovered`
- `test_ack_keeps_an_earlier_outstanding_claim_in_flight`
- `test_compacting_session_keeps_the_notice_and_lands_it_in_the_new_tip` (0793)
- `test_unloaded_sweep_recovers_an_orphaned_claim`

These use real SQLite stores, a dead subprocess for orphan identity, and a stubbed notification submit. A live backend replacement, full compaction run, end-to-end row adoption by an agent turn, poller-stop release, and remote-gateway delivery are untested by this suite.

### Applying

Append 0792 then 0793 in series order. The existing database migration adds nullable in-flight fields. Restart the backend to load the code; no plugin or new configuration is required.

### Gotchas

At-least-once recovery plus transcript deduplication is not a guarantee that the agent completed the requested follow-up. Remote-only notices and sessions without a usable transcript store fall back to the prior submit path; the new local durability tests do not cover that fallback.

Patch 1597 below qualifies "a live holder blocks another durable claimant": a live holder's claim stops blocking once it is provably stuck.

## A live holder's never-acked claim no longer blocks the owner (1597)

Incident: a card's completion never reached the chat that made it. A long-lived non-owner process (a `hermes
dashboard`) still held that chat's sub row, `inflight_from` far behind `last_event_id`, and had never acked it.
0792 lets only a dead holder be recovered, so the chat's owning backend could not claim while this live process
held the row. Every 5 min the dashboard re-claimed the new events and was refused `SESSION_NOT_OWNED`. It rewound
only its latest range (`rewind_notify_cursor`), so the earlier marker stayed. Nine more rows on the same pid were
stuck the same way.

### Behavior

- **Refused non-owner releases its whole marker.** When the turn is refused because another process owns the chat
  (the pre-turn owner check, or a refused submit for which `_notif_owner_refused` holds), each local subscription
  this backend no longer buffers anything for is released whole by `release_notify_claim`. That covers any
  earlier claim it never acked. The cursor goes back to the earliest unacked event, and the owner's next poll
  claims and delivers it.
- **Stopping poller releases too.** `_kb_release_claims` also releases the whole marker it holds for every local sub
  in `_kanban_claims` (e.g. a durable claim it never acked), not only the undelivered ranges.
- **Stuck live claims stop blocking.** In `claim_unseen_events_for_sub`, a live foreign holder's marker is rewound
  like an orphan (WARNING logged with the reason) when either:
  - its held range `(inflight_from, last_event_id]` has no deliverable event. The new `deliverable_kinds` parameter
    (the tui poller and the unloaded sweep pass the kinds that render a notice) defaults to `kinds`. A
    silent-only range (`unblocked`/`archived`) will never be acked.
  - it is older than `NOTIFY_CLAIM_STALE_S` (15 min). The age is read from the new `inflight_at` column. A marker
    written before that column (NULL) is dated by the newest event it covers.
  A fresh live holder of a deliverable range still blocks. Stealing a stale claim is safe because the notice row
  is deduped by event key.
- **Leaks closed:**
  - The unloaded sweep now acks a silent-only claim. Before, it returned without an ack and left the range in
    flight under its live process.
  - The live poller keeps a silent claim whose ack was skipped behind an earlier pending claim, marked durable, so
    the next ack run covers it. Before, it dropped the claim.
  - `_kb_mark_delivered` and `_kb_rewind_refused` keep durable claims that are not yet acked instead of dropping
    them.
- `ack_notify_claim` now returns whether it cleared or moved the marker.

### How it works

`kanban_notify_subs` gains `inflight_at` (schema, migration column list and rebuild spec). A claim sets it when it
opens a marker and keeps the earliest value while re-claiming, so a holder that keeps re-claiming without acking
still ages out. Ack, rewind and orphan rewind clear it. `_stuck_live_claim` returns the reason or `None`.
`_rewind_orphan` gains `why` for the log line. `release_notify_claim` rewinds the row only when this process is the
holder (no-op otherwise). `_kb_rewind_refused(..., not_owned=True)` computes the subs it no longer holds claims for
and releases each one.

### Code

`hermes_cli/kanban_db.py`, `hermes_cli/kanban_db_connect.py`, `hermes_cli/kanban_db_notify.py`,
`tui_gateway/kanban_unloaded.py`, `tui_gateway/session_notifications.py`.

### Tests

`tests/tui_gateway/test_kanban_notice_live_holder.py` (new; real kanban DB and SessionDB, stubbed
`_run_prompt_submit`, a second backend simulated by patching `_my_holder` to the parent pid):

- `test_refused_live_holder_releases_its_whole_marker_and_the_owner_claims_next_tick`
- `test_live_holder_of_a_range_with_nothing_deliverable_does_not_block`
- `test_stale_live_holder_marker_is_recovered` (parametrized `inflight_at`, `legacy-null-inflight_at`)
- `test_fresh_live_holder_of_a_deliverable_claim_still_blocks`
- `test_unloaded_sweep_acks_a_silent_only_claim`
- `test_silent_claim_behind_a_pending_claim_is_acked_with_it`

`tests/tui_gateway/test_kanban_notice_at_least_once.py`: `test_live_holder_blocks_other_claimers_and_is_not_recovered`
is adjusted. It now appends a deliverable `blocked` event behind the held completion instead of resetting the
cursor, so the held range stays deliverable and fresh and the claim keeps blocking.

These were not rerun by the mirror pass.

Untested: the refused-submit path (`_notif_owner_refused` after an accepted-then-refused turn; only the pre-turn
owner check is exercised); `_kb_release_claims` releasing the whole marker on poller stop; the `_kb_mark_delivered`
and `_kb_rewind_refused` changes that keep unacked durable claims (not isolated); remote-notice claims; two real
backend processes (the second holder is simulated).

### Applying

Apply after 0792/0793. The `inflight_at` column is added by the existing notify-sub column migration (nullable; old
markers fall back to event age). Restart backends to load the code.

### Gotchas

- `NOTIFY_CLAIM_STALE_S` is a constant, not config. A healthy claim is acked within one turn wait, but a holder that
  legitimately waits longer than 15 min on a deliverable claim can have it taken over. Duplicate rows are prevented
  by the event-key dedup, not by the claim.
- `release_notify_claim` only acts on markers this process holds. A different live non-owner's marker is cleared
  only by the stuck-claim rules when someone else claims.

## Plugin?

No: the claim/submit sequence is inside the poller; a plugin can't see the claim.
