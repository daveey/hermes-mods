# Typed /loop tick rows

Patches: **3032, 3039**.

## Behavior
- A slash `/loop` tick's submitted prompt is stored as a `loop_tick` row with a language-neutral display line (the command name) instead of an untyped user row, in the TUI and the gateway; Desktop and TUI history project it as a loop line, not a user bubble.
- The context compressor treats a loop tick as a new turn, not human input.
- A producer card can never name `loop_tick`: `PRODUCER_CARD_KINDS` is an explicit four-kind list while `loop_tick` stays silence machinery.

## How it works
- 3032 types the tick in the TUI slash-tick handler and the gateway wakeup fire, adds `loop_tick` to `PRODUCER_NOTICE_DISPLAY_KINDS`, updates hydration and types in Desktop, and limits the relay's legacy first-untyped-row skip to when no typed tick exists.
- 3039 pins `PRODUCER_CARD_KINDS` explicitly so it is no longer derived from the notice set.

## Code
- `gateway/response_filters.py`
- `gateway/run_goals.py`
- `agent/context_compressor_turns.py`
- `hermes_cli/cli_agent_setup_mixin.py`
- `apps/desktop/src/lib/chat-messages/hydration.ts`
- `apps/desktop/src/types/hermes.ts`
- `apps/desktop/e2e/core/oracle.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/src/lib/chat-messages.test.ts`: `it 'renders a /loop slash tick prompt as its loop line, not a user bubble'`.
- `tests/hermes_cli/test_kanban_relayed_human.py`: `test_a_completed_typed_slash_tick_row_is_skipped`, `test_a_reply_during_a_typed_slash_tick_is_the_reply`.
- `tests/hermes_cli/test_loop_tick_display.py`: `test_loop_tick_display_names_the_command_not_the_expanded_prompt`, `test_tui_slash_tick_submits_its_prompt_typed`, `test_gateway_slash_tick_event_persists_as_loop_tick`, `test_compressor_treats_a_loop_tick_as_a_new_turn_and_not_human_input`, `test_history_projection_keeps_the_loop_tick_kind_and_its_line`, `test_cli_resume_recap_shows_the_loop_line_not_the_prompt`, `test_a_loop_tick_is_not_an_automation_replay_turn`.
- `ui-tui/src/__tests__/messages.test.ts`: `it 'projects a /loop slash tick prompt as its loop line, never the expanded prompt'`.
- `tests/gateway/test_automation_notification_category.py`: `test_producer_card_kinds_is_explicit_and_excludes_loop_tick`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
