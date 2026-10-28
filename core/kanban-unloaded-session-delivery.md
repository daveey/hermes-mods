# Kanban results reach chats no backend holds (+ green row for waiting results)

Patches: 0185 (delivery + reap warning), 0186 (`kanban_unseen` stripe input), 0596 (a chat mid-turn defers the
notice instead of crashing the sweep; overdue warning; HX014 code-health rule).

## Behavior
- Card completion/blocked/crash notices for a `platform=tui` subscription are written into that chat's transcript
  even when no backend has the chat loaded (websocket drop, parent-death watchdog, `startup_orphan_reap`). The notice
  is a `role=user` row with `display_kind="kanban_notice"` (renders as a system line), the row is marked unread and
  its last-activity bumped, so the sidebar row turns green with no typing.
- Session rows carry `kanban_unseen`: finished cards whose result no chat has been handed yet. With no running cards
  it paints the row green; running cards (`kanban_active`) keep it `background`. An orphan-reaped idle session with
  turns is `done` (green), never working/stalled.
- `startup_orphan_reap` logs a WARNING naming reaped chats that still hold open kanban subscriptions.
- A chat running a turn in another backend refuses the notice row. The sweeper treats that as "busy, try later": it
  rewinds the claim, logs one INFO line (`... deferred: chat busy (active turn); retrying next pass`, DEBUG on later
  passes, no traceback) and delivers on the first pass after the turn ends. A notice whose claim was rewound and that
  still waits 15 min after its event logs ONE WARNING per board/card/chat: `kanban notice for <board>/<task> to chat
  <id> overdue <N>m: <reason>`. Before 0596 the store opener swallowed the refusal at its `yield` and Python raised
  "generator didn't stop after throw()", logged with a traceback every 30 s (277 times in 2h20m on 10-27).

## How it works
- `tui_gateway/kanban_unloaded.py` runs a sweeper thread in every gateway process (30 s; first pass after 20 s).
  It skips chats this process holds, and events younger than a 30 s grace (a live poller anywhere claims within ~5 s).
  It claims with the same atomic cursor CAS as the live poller (`claim_unseen_events_for_sub`), so a range is claimed
  once; a failed or refused append rewinds the claim.
- `find_session_db` locates the store inside its `try` and yields outside any handler (an `ExitStack` keeps the
  store open across the `yield`), so an error raised in the caller's block propagates as itself.
  `_persist_notice` returns False on `SessionTurnLeaseLostError` (busy) and raises on anything else; both rewind.
  `_sweep_sub` records why a claim was rewound and calls `_warn_if_overdue` (`OVERDUE_S = 900`) when nothing was
  delivered; the record clears once the sub has no unseen events.
- `scripts/code_health` rule HX014 (`py_rules_contextmanager.py`) blocks new `@contextmanager`s whose broad `except`
  around a `yield` does not re-raise and can yield again (a second `yield`, or the `try` in a loop). One-shot
  suppress-and-log helpers are not flagged; the tree has 0 hits after 0596.
- `SessionDB.append_kanban_notice` dedupes by `notice_id` (board:task:cursor range) inside the writer txn, refuses
  while another backend holds a live turn lease, and stamps a NULL `last_read_at` just before the notice.
- `kanban_session_activity._read_unseen` counts tui subs whose cursor trails a terminal event.
- Knobs (env): `HERMES_KANBAN_UNLOADED_SWEEP_S` (0 disables), `..._GRACE_S`, `..._FIRST_S`.

## Code
`tui_gateway/kanban_unloaded.py`, `hermes_state_messages.py` (`append_kanban_notice`), `tui_gateway/session_reaper.py`,
`hermes_cli/kanban_session_activity.py`, `apps/desktop/src/store/session-dot-state.ts`
(`$kanbanUnseenSessionIds`, `withRowActivity`), `lib/chat-messages/hydration.ts`, `types/hermes.ts`.

## Tests
- `tests/tui_gateway/test_kanban_unloaded_sweep.py`: notice persisted in one sweep, exactly once, row unread;
  live poller vs sweeper never double deliver (both orders); loaded session skipped; grace respected; failed append
  rewinds and retries; unknown chat keeps cursor; dedupe; reap warning.
- `tests/hermes_cli/test_kanban_session_activity.py`: `kanban_unseen` counting.
- `tests/tui_gateway/test_kanban_unloaded_busy.py` (0596; REAL `find_session_db` and a real turn lease): an error in
  the caller's block comes out as itself, not RuntimeError; store-open errors are still skipped; a busy chat
  rewinds with no WARNING/traceback and lands after the turn (passive-row path); same via the wake-worker path
  (refused wake -> busy row -> `deferred`); a stall past 15 min warns exactly once over 3 passes. Red 3/4 on the base.
- `tests/scripts/test_code_health_contextmanager_rule.py`: HX014 shapes (flagged and not), the flagged shape really
  turns the caller's error into RuntimeError while the fixed shape does not, rule scope excludes tests.
- Live-path repro (0596, isolated HERMES_HOME, real sweeper thread, a second process holding the turn lease for ~6
  passes): base 6 WARNINGs / 6 "didn't stop after throw()"; fixed 0 WARNINGs / 0 tracebacks / 1 INFO deferral; both
  deliver once after the turn ends.
- `session-dot-state.test.ts`: results waiting -> green; cards running -> background; orphan-reaped idle -> green.
- Live check (scratch HERMES_HOME, real sweeper thread, no typing): row `kanban_unseen=1` before, notice in
  transcript within ~1 s and `unread=True` after, nothing duplicated over further sweeps.

## Applying
`git am -3 --empty=keep` patches 0185 then 0186 (0186 only touches the row stamp and renderer; 0185 stands alone).
0596 also needs 0518 (the wake worker it touches).

## Gotchas
- The sweeper only handles chats found in a local state.db (launch store, then named profiles); a chat on another
  gateway is swept by that gateway.
- Since 0518 the sweeper first tries a turn with the notice and writes this row only as the fallback ([kanban-unloaded-sweep-wake.md](kanban-unloaded-sweep-wake.md)). Before it, the notice was a transcript row, not an agent turn: the agent read it only when the chat was next opened.

## Plugin?
No plugin surface exists for gateway-side delivery or sidebar stripe inputs.
