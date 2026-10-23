# kanban-honest-done

A Kanban worker can't fake "done".

## Why

A goal-mode card couldn't run its live acceptance (the target machine was offline). The worker ran
`hermes kanban edit` on its **own** card to drop that acceptance from the body, retitled it
"… (shipped; …run split to follow-up)", and completed 10 s later. No follow-up card existed. The goal judge accepted
the narrowed body, every card in the chat was done, and the chat went green while the work wasn't done.

## What it does

Inside a dispatcher-spawned worker (`HERMES_KANBAN_TASK` set), a `pre_tool_call` hook refuses:

| Gate | Refuses | Paths |
|---|---|---|
| A | editing your **own** card's title/body (`--priority`, other cards, comments stay allowed) | `hermes kanban edit <own id or $HERMES_KANBAN_TASK> --title/--body` via `terminal`, `process`, `execute_code`, `tool_call` |
| B | completing your own card when the summary (or a title that says "split to follow-up" / "dropped from scope" / "deferred") admits dropped, deferred, unrun or unverified work, unless the summary names a follow-up card `t_xxxxxxxx` that exists (any board), is open or done (not archived), and was created by this card (`creator_task_id`) or is linked to it | `kanban_complete` tool (direct or via `tool_call`), `hermes kanban complete <own> --summary/--result` via shell or `execute_code` |

Each refusal says what to do: finish the missing part, or `kanban_create` the follow-up (`parents=[<this card>]`)
and name its id, or `kanban_block` with the exact physical step. Completing another card (a reviewer approving a
card in review) is not gated.

C. In the Desktop backend, `register()` also registers `core.needs_attention` with the fork core's
`kanban_session_activity.register_done_check` (see `core/kanban-false-done-attention.md`): a chat whose card
finished in the last 24 h with such a handoff and **no** follow-up card (created by / linked to it, not archived,
or named) stripes amber instead of green. `honest-done: ack` in a comment on the card clears it (the human accepted
the narrower result). Older cores without the registry just skip C.

Classifier: per sentence, quoted text removed, questions skipped; negations ("no follow-ups", "nothing deferred",
"deferred at X's request", "deployment is deferred") don't count; follow-up commits/PRs are finished work. Patterns
are `DROP_PATTERNS` in `core.py`.

## Replay

```
python3 replay.py --days 14 [--json out.json] [--task t_xxxxxxxx]
```

Read-only over every board: completed cards whose handoff admits dropped work, and which of those have no
follow-up card. On the author's boards (Oct 2026, 14 days): 1345 completed cards, 140 admitted dropped work, 37 had
no follow-up card; spot-check precision of those 37 was about 85 % (misses: domain uses like "trust stays
unverified", scoping notes like "the other test files were not run").

## Tests

```
cd plugins/kanban-honest-done && uv run -q --with pytest python -m pytest -q -p no:cacheprovider tests
```
