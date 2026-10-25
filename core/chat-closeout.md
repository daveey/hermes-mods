# Chat close-out seams: waiting stripe, waiting sources, wake-a-turn

Patches:

- `patches/0244-feat-kanban-rows-carry-kanban_waiting_on-ids-server..patch`: session rows carry `kanban_waiting_on`; `server._wake_session_turn` runs one turn in an unloaded chat.
- `patches/0245-feat-desktop-grey-waiting-stripe-card-glyph-while-a-.patch`: the sidebar's grey `waiting` stripe (card glyph).
- `patches/0246-feat-kanban-plugin-waiting-sources-keep-a-chat-grey-.patch`: `register_waiting_source` for plugins; the wake goes through the shared turn admission.

These are the two core seams the public plugin `plugins/chat-closeout` uses: a chat that hands work to Kanban cards
stays visibly "waiting" until it has closed the work out, and the plugin can start that close-out turn without the
user typing.

## Behavior

- Every session row carries `kanban_waiting_on`: the ids of the open cards (queued, running, or waiting on a human) the
  chat created or shepherds, over the whole session lineage, capped at 50.
- The sidebar paints a grey `waiting` stripe with a card glyph (tooltip "Waiting on N cards") while `kanban_active` > 0
  and no turn is live in the chat. Stronger states still win: a human-blocked card, a user-blocked chat and stuck states
  paint red. Cmd+] / Cmd+[ skip a `waiting` row the same way they skip `periodic` rows.
- `kanban_session_activity.register_waiting_source(name, fn)` lets a plugin add card ids per session (`fn()` returns
  `{session_id: ids}`). The extra ids count into `kanban_active` and `kanban_waiting_on`, without double counting ids the
  boards already count. A source that raises is swallowed (the rest still count); `fn=None` removes the source.
- `tui_gateway.server._wake_session_turn(key, text, display_kind=, display_metadata=, label=)` runs ONE agent turn in a
  chat without the user typing. It uses the chat if it is live in this backend, otherwise `session.resume`s it
  in-process (walking the profiles when the chat is not in the default store, port 4007 backend). It claims the turn
  through `_notif_claim_turn`, the admission gate every notice turn uses (backend retirement + the session history
  lock), so it never double-admits against a user prompt or another waker and nothing starts after the user's Stop.
  Returns one of `started`, `busy`, `refused`, `missing`, `error`; it never raises.

## How it works

`hermes_cli/kanban_session_activity.py` already counted per-session cards from each board. `waiting_on_task_ids()` now
returns the open ids per session from the same cached read, `annotate_kanban_activity` stamps `kanban_waiting_on`
next to the counts, and `_extra_waiting()` merges plugin sources (only ids the boards do not already count) before the
counts are stamped. On the Desktop, `session-dot-state.ts` gains a `waiting` state ordered below the red/blocked states
and above idle; `sidebar-visible-order.ts` shares the predicate so stepping skips it; `session-row.tsx` and
`session-tab-accent.tsx` draw the grey stripe and card glyph.

`_wake_session_turn` in `tui_gateway/session_notifications.py` finds the live session, or resumes it by calling the
`session.resume` RPC handler in-process, then submits the prompt with the display kind/metadata/label the transcript
shows for an automated turn. A refused or failed owner slot releases the claim.

## Code

- `hermes_cli/kanban_session_activity.py` (`waiting_on_task_ids`, `register_waiting_source`, `_extra_waiting`).
- `tui_gateway/session_notifications.py` (`_wake_session_turn`, exposed as `tui_gateway.server._wake_session_turn`).
- `apps/desktop/src/store/session-dot-state.ts`, `sidebar-visible-order.ts`, `sidebar-automated.ts`.
- `apps/desktop/src/app/chat/sidebar/session-row.tsx`, `apps/desktop/src/app/chat/session-tab-accent.tsx`.
- `apps/desktop/src/i18n/en.ts` (waiting tooltip), `apps/desktop/src/types/hermes.ts` (`kanban_waiting_on`).

## Tests

- `tests/hermes_cli/test_kanban_session_activity.py`: rows carry the open card ids (done cards drop out); a plugin
  source keeps a settled batch waiting until its close-out, is counted once, survives a failing source, and unregisters.
- `tests/tui_gateway/test_session_wake_turn.py`: an unloaded chat is resumed then runs one turn; a chat live here is
  not resumed; a busy or stopped chat is left alone; a resume refused by another owner; a chat in no store is
  `missing`; an owner-slot refusal releases the claim; the wake goes through the shared admission (retiring backend
  never starts the turn).
- `apps/desktop/src/store/session-dot-state.test.ts`, `apps/desktop/src/app/chat/sidebar/session-row.test.tsx`: the
  waiting stripe and its precedence under red states.
- Not re-run when this doc was written (untested here). Wake-by-REST/terminal entry paths do not exist; the only entry
  is the in-process function.

## Applying

`git am -3` in order, after 0231 (it extends the activity module that 0215 and 0231 changed). Run
`npm run i18n:keys` after taking 0245 if `en.ts` conflicts.

## Gotchas

- Plugin sources run on every row annotation: keep `fn` cheap and cached.
- `kanban_waiting_on` is local-board ids only; cards on another gateway count in `kanban_active` (0215) but are not
  listed.
- The wake never raises: callers must read the returned status, not catch exceptions.

## Plugin?

Yes: the public plugin `plugins/chat-closeout` uses both seams (waiting source + wake).
