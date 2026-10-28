# A refused Kanban notice is handed back, not dropped

Patch:

- `patches/0132-fix-tui-hand-a-refused-kanban-notice-back-to-the-cha.patch`: backend
  (`tui_gateway/session_notifications.py`).

What you see: a chat open in two Hermes backends (Desktop plus a phone client, or two Desktop windows) still gets
every Kanban "✔ done" / "⏸ blocked" notice for the cards it made. Before, one of them could vanish: the agent never
heard that a child card finished, so a "send it when it's ready" follow-up never ran.

## Behavior

The following describes 0132's refusal path. Patches 0720–0721 below supersede it for local notices already recorded durably: those rows are not rewound on a turn refusal.

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

- Patches 0720–0721 add durable claims and transcript deduplication for local tui subscriptions; see below. Remote-only/unkeyed notices retain the previous submit path and are not covered by the new backend-replacement tests.
- A durable transcript row proves delivery to the chat, not successful completion of the agent's follow-up work. The `follow-through` plugin remains a separate check on that work.

## Durable local notices across backend replacement (0720–0721)

Patch **0720** (fork `bf9ca96750`) keeps a busy chat's local tui notice claim in flight until the notice is recorded in the transcript. A replacement backend can recover a dead holder's claim rather than trusting the already-advanced cursor. Patch **0721** (fork `9cf6985e99`) adds the compaction regression test; it changes no production code.

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
- `test_compacting_session_keeps_the_notice_and_lands_it_in_the_new_tip` (0721)
- `test_unloaded_sweep_recovers_an_orphaned_claim`

These use real SQLite stores, a dead subprocess for orphan identity, and a stubbed notification submit. A live backend replacement, full compaction run, end-to-end row adoption by an agent turn, poller-stop release, and remote-gateway delivery are untested by this suite.

### Applying

Append 0720 then 0721 in series order. The existing database migration adds nullable in-flight fields. Restart the backend to load the code; no plugin or new configuration is required.

### Gotchas

At-least-once recovery plus transcript deduplication is not a guarantee that the agent completed the requested follow-up. Remote-only notices and sessions without a usable transcript store fall back to the prior submit path; the new local durability tests do not cover that fallback.

## Plugin?

No: the claim/submit sequence is inside the poller; a plugin can't see the claim.
