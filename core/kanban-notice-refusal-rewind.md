# A refused Kanban notice is handed back, not dropped

Patch:

- `patches/0132-fix-tui-hand-a-refused-kanban-notice-back-to-the-cha.patch`: backend
  (`tui_gateway/session_notifications.py`).

What you see: a chat open in two Hermes backends (Desktop plus a phone client, or two Desktop windows) still gets
every Kanban "✔ done" / "⏸ blocked" notice for the cards it made. Before, one of them could vanish: the agent never
heard that a child card finished, so a "send it when it's ready" follow-up never ran.

## Behavior

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

- The in-memory buffer still dies with its backend: a notice claimed by a backend that is killed before its turn
  runs is lost. The `follow-through` plugin (`plugins/follow-through/`) covers that case by detecting completed
  cards whose notice never reached the chat.

## Plugin?

No: the claim/submit sequence is inside the poller; a plugin can't see the claim.
