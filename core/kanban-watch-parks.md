# Kanban watch parks

Patch **2073**, source `081be082652487cb6ab42676880d947c9772826f`.

## Behavior

- The `kanban_schedule` tool handler accepts `watch=true` for a later check that needs no action from the chat meanwhile. While scheduled, it is omitted from local active-card counts, parked counts and the session's open-card list.
- The notification subscription remains. Waking makes the card count again; the regression exercises a subsequent human block and its unseen notice.
- Omitted `watch` retains the previous scheduled event's flag; explicit `watch=false` clears it.
- A non-watch park more than 24 hours away returns `chat_hold`, identifying the held interactive chat and wake date. Short parks omit the hint.
- Completing a scheduled card without a run returns `not_running` and suggests archiving with a comment or re-parking as a watch, rather than reporting a stale run.

## How it works

The schedule handler resolves the flag against the last scheduled event, validates boolean input and passes it into the scheduling transaction. The event payload stores `watch: true`. SQL predicates exclude a card only while its status is scheduled and its newest park event is a scheduled watch. Older-schema fallback logic is present but untested by this pass.

## Code

- `hermes_cli/kanban_watch.py`: watch predicates, sticky flag, held-chat lookup and hint.
- `hermes_cli/kanban_db_schedule.py`: transactional event payload.
- `hermes_cli/kanban_session_activity.py`: count and list filters.
- `hermes_cli/kanban_session_activity_card_details.py`: extracted existing detail projection.
- `tools/kanban_tools.py`, `tools/kanban_tools_schemas.py`: tool argument, result and completion refusal.
- `tools/kanban_tools_park_args.py`: extracted wake-check argument helpers.

## Tests

`tests/tools/test_kanban_watch_park.py` exercises the tool handlers against a disposable board:

1. `test_watch_park_frees_the_chat_and_keeps_the_notice`: active/parked/list exclusion, subscription retention, wake counting and later human-block notice.
2. `test_watch_is_sticky_across_a_repark_without_it`: omitted flag retention and explicit clearing.
3. `test_long_park_without_watch_names_the_chat_it_holds`: long-park hint and continued hold.
4. `test_short_park_has_no_hint`: short-park result.
5. `test_complete_on_scheduled_card_says_not_running`: typed refusal and recovery guidance.

See [snapshot execution evidence](mirror-2073-verification.md). Live notification delivery, remote gateway projection, Desktop rendering, CLI/REST, terminal-tool and execute_code end-to-end paths are **untested** in this mirror pass. The test proves the persisted unseen notice, not an external delivered message.

## Applying

Use the full ordered snapshot and bridge slices in [README Applying](README.md). This patch touches shared scheduling and activity modules; standalone application is not certified.

## Gotchas

A watch is still scheduled work, not verified completion. It does not waive wake checks, producer requirements or human-action rules. Once awake it holds the chat again. The sticky behavior is resolved by the schedule tool handler; direct database calls use their explicit boolean default.

## Plugin?

No. This changes core scheduling and session activity projections.
