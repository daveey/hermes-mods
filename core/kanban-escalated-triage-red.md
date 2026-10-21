# Block-loop-escalated triage card paints its session red

Patches:

- `patches/0161-fix-kanban-a-block-loop-escalated-triage-card-paints.patch`: `BLOCKED_STATUSES` gains `triage`, filtered by human `block_kind`.

What you see: when block-loop detection moves a card to triage, the chat that owns it turns red again, because it still waits on you.

## Behavior

- A triage card with a human `block_kind` (`needs_input`, `capability`, or none-with-kind set) counts in `kanban_blocked` and is listed on its session. A card born in triage for a specifier (`block_kind` NULL) is not red.
- Boards without the `block_kind` column keep upstream behavior (`blocked` only).

## How it works

- `hermes_cli/kanban_session_activity.py`: `BLOCKED_STATUSES = ("blocked", "triage")`; `_status_filter` ORs `blocked AND human` with `triage AND block_kind IN HUMAN_BLOCK_KINDS`.

## Code

`hermes_cli/kanban_session_activity.py`.

## Tests

`tests/hermes_cli/test_kanban_session_activity_triage.py`: `blocked_task_counts()` counts the escalated and human-blocked sessions but not the specifier-bound or transient ones; `session_tasks` lists the escalated card.

## Applying

Builds on the human-block split from 0155 ([kanban-timed-waits.md](kanban-timed-waits.md)) and the escalation in [kanban-block-loop-escalation.md](kanban-block-loop-escalation.md).

## Gotchas

- The Blocked sidebar section ([kanban-blocked-sidebar.md](kanban-blocked-sidebar.md)) reads its own list and is not changed by this.

## Plugin?

No. The count comes from core's session-activity query.
