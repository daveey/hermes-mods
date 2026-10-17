# Kanban workers schedule timed waits instead of blocking

Patches:

- `patches/0113-fix-kanban-route-timed-waits-to-kanban_schedule-not-.patch`: agent/tools. Worker guidance and the
  `kanban_block` description send timed or external waits to `kanban_schedule`.

What you see: a card waiting on a run cap, a soak or a release window goes to `scheduled` instead of `blocked`, so the
chat that created it no longer turns red as if it needed you.

## Behavior

- `KANBAN_GUIDANCE` (worker system prompt) gains a rule: when waiting on time or an external event, call
  `kanban_schedule(reason="SCHEDULED_UNTIL=<ISO8601> — what you're waiting for")`, not `kanban_block`.
- The `kanban_block` tool description says waiting on time or an external event is not a block and names
  `kanban_schedule`, because a blocked card marks its chat as needing a human.
- `kanban_block` itself behaves as before; only the guidance changed.

## How it works

- `agent/prompt_builder.py`: extra text in `KANBAN_GUIDANCE`.
- `tools/kanban_tools_schemas.py`: extended `kanban_block` description.

## Tests

`tests/agent/test_system_prompt.py`: `test_kanban_guidance_routes_timed_waits_to_schedule_not_block` (guidance names
`kanban_schedule(` and `SCHEDULED_UNTIL=`). The `kanban_block` description wording has no test (untested).

## Applying

Standalone. Needs the `kanban_schedule` tool, which exists upstream.

## Gotchas

- This is prompt guidance, so a worker can still call `kanban_block` for a wait.

## Plugin?

Partly: a plugin could add guidance, but the worker prompt and the core tool description are core text.
