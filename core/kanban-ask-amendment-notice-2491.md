# Metadata-only ask amendments no longer re-send a blocked notice

Patches: **2455**.

## Behavior

`hermes kanban block <id> ... --human-request <json> --metadata-only` amends the ask of a card that is already parked and records a `blocked` event with `metadata_only: true`. Both notice paths used to render that as a fresh "blocked — actor" notice, even to the chat that made the edit, so the user saw a new block and a repeated ask. Now:

- The subscriber chat that made the edit gets nothing (no message, no wake); its notification cursor still passes the event.
- Other subscribers get one line: `ask updated (card still blocked): <new action> — open task: <link>`.
- If the card is no longer parked at delivery time, the notice reports its current state as `block_notice` does.
- Real block transitions are unchanged, including to the actor's own chat.

## How it works

`kanban_block_notice` gains `is_metadata_only(payload)`, `actor_is_chat(actor, chat_id)` and `ask_update_notice(...)` (returns `None` to stay silent). The actor label is `agent (chat <session>)`, where the session is either a raw session id or a gateway DM session key `agent:<ns>:<platform>:dm:<id>`; subscriptions store the bare `<id>`, and `actor_is_chat` matches both. Only DM keys are reduced to their chat id; group and thread keys never match by suffix. The gateway notifier (`gateway/kanban_watchers_notifier.py`: `_fmt_blocked`, plus `_own_ask_amend` filtering events out of `_KanbanNotification` before pinging or waking) and the TUI/Desktop path (`tui_gateway/session_notifications._format_kanban_event_text`) both call `ask_update_notice`. Related: [kanban-block-instructions.md](kanban-block-instructions.md) and [kanban-actionable-block-notices.md](kanban-actionable-block-notices.md) cover the notices this refines.

## Code

`hermes_cli/kanban_block_notice.py`, `gateway/kanban_watchers_notifier.py`, `tui_gateway/session_notifications.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/gateway/test_kanban_notifier.py` (gateway notifier end to end through the real metadata-only writer: silent for the editor with cursor passed, ask-updated line for another subscriber, real block unchanged), `tests/tui_gateway/test_actionable_block_notices.py` (renderer on both the gateway `_fmt_blocked` and TUI `_format_kanban_event_text` paths, actor/chat id matching table), `tests/hermes_cli/test_kanban_cli_actor.py` (CLI `block` with and without `--metadata-only`, rendered through the TUI formatter). Other delivery channels (for example remote-card notices) are untested for this behavior.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Silence depends on the actor label naming the subscriber's chat; an edit made by a non-chat actor (such as `user` or a task) notifies every subscriber.
- A group or thread session key as actor never matches, so that chat is notified.
- The remote-card notice formatter still shows the actor for blocks (a test asserts it); only the two paths above changed.

## Plugin

Core change; no plugin required.
