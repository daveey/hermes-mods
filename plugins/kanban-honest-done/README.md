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
| B | completing your own card when the handoff, meaning the summary **and every text value in its `metadata`** (`not_done`, `remaining`, nested lists), admits dropped, deferred, unrun or unverified work (incl. plain "not verified / not performed / not checked / not confirmed / not tested"), unless the handoff names a follow-up card `t_xxxxxxxx` that exists (any board), is open or done (not archived), and was created by this card (`creator_task_id`) or is linked to it | `kanban_complete` tool (direct or via `tool_call`), `hermes kanban complete <own> --summary/--result/--metadata` via shell or `execute_code` |
| D | completing your own card when its body or comments name an origin chat (`origin … @session:X`, e.g. "Origin chat: @session:default@macbook/X") that is not linked to the card: no unreleased `task_chat_links` row, no notify subscription (`tui` X, or `hub` `<gw>/<profile>/X`), and no `follow-through: SENT … X` comment. The refusal tells the worker to send the chat the result with `cross_session` and record that comment (workers are fenced from `hermes kanban notify-subscribe` on their own board), or `kanban_block` if the send fails. Double-quoted text is skipped | same paths as B |
| E | (any session, not only workers) `kanban_create` whose title/body names an origin chat on **another** gateway (`origin … @session:<profile>@<gw>/<id>`, `<gw>` a known hermes-hub gateway) without `origin_session` (or `HERMES_ORIGIN_SESSION`) binding that chat. The refusal gives the exact `origin_session='…'` to pass, which subscribes the chat so it sees the card and its completion. Fails open when this gateway has no hermes-hub name | `kanban_create` tool (direct or via `tool_call`) |

Each refusal says what to do: finish the missing part, or `kanban_create` the follow-up (`parents=[<this card>]`)
and name its id, or `kanban_block` with the exact physical step. Completing another card (a reviewer approving a
card in review) is not gated.

C. In the Desktop backend, `register()` also registers `core.needs_attention` with the fork core's
`kanban_session_activity.register_done_check` (see `core/kanban-false-done-attention.md`): a chat whose card
finished in the last 24 h with such a handoff and **no** follow-up card (created by / linked to it, not archived,
or named) stripes amber instead of green. `honest-done: ack` in a comment on the card clears it (the human accepted
the narrower result). Older cores without the registry just skip C.

Classifier: titles are request context, not evidence of unfinished work; only the current handoff is classified.
Per sentence, quoted text removed, questions skipped; negations ("no follow-ups", "nothing deferred",
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

`tests/test_title_context.py` covers the exact deferred-cleanup defect title: completed handoffs pass,
while real summary/nested-metadata admissions are refused through the tool, tool wrapper, terminal, and
triple-quoted execute_code paths. It also checks explicit title overrides and sidebar attention.

Replay with metadata, "not verified" and Gate D (hub, Oct 28 2026, 14 days, 399 completed cards): B would refuse 15
(was 2; the new ones are real admissions such as "CI not yet checked", about 2 false); D would refuse 16, every one a
card naming an origin chat that was never linked to it (1 is a self-heal write-up that only describes the chat).

Why D: a card whose plan said "report to origin chat @session:…" finished with that chat named only in prose. The
done ping went to the supervisor chat, and the origin chat kept saying "waiting on CI" for 14 h.

```
cd plugins/kanban-honest-done && uv run -q --with pytest python -m pytest -q -p no:cacheprovider tests
```
