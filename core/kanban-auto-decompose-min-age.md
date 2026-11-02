# Auto-decompose waits for a triage card's grace window

Patches: **1630** (`e5654de882`, `fix(kanban): auto-decompose sweep waits kanban.auto_decompose_min_age_seconds (120 s)
before taking a triage card`), **1631** (`1ee4a716d7`, docs row for the setting).

Related: [kanban-block-loop-escalation.md](kanban-block-loop-escalation.md) (which triage cards are never auto-decomposed).

## Behavior

- Agents sometimes create a triage card meant as a hold (a test fixture, a "park it for later" card) and park it with
  `kanban_schedule` right after. The dispatcher's auto-decompose sweep could specify such a card within seconds of
  creation and a worker then claimed it.
- The sweep now skips triage cards younger than `kanban.auto_decompose_min_age_seconds` (default `120`; `0` restores the
  immediate sweep). A card scheduled inside the window stays scheduled. Once older, it is specified as before.
- Manual `hermes kanban decompose <id>`, `decompose --all` and the dashboard's Decompose button ignore the window.
- A card whose `created_at` cannot be parsed stays a candidate (excluding it would strand it).
- The `kanban_create` tool's `triage` parameter now says triage is not a hold and to park with `kanban_schedule`.

## How it works

`gateway/kanban_watchers_common._auto_decompose_min_age_seconds()` re-reads the setting each tick (a read error logs a
warning and keeps 120). `_KanbanDispatcher.auto_decompose_tick(per_tick, min_age_seconds=None)` stores it and
`_decompose_board` passes it to `kanban_decompose.list_triage_ids(..., min_age_seconds=...)`, which drops rows with
`created_at > now - min_age`. The default lives in `hermes_cli/config_defaults.py`; 1631 adds the row to
`website/docs/user-guide/features/kanban.md`.

## Code

`gateway/kanban_watchers_common.py`, `gateway/kanban_watchers_dispatcher.py`, `hermes_cli/config_defaults.py`,
`hermes_cli/kanban_decompose.py`, `tools/kanban_tools_schemas.py`, `website/docs/user-guide/features/kanban.md`.

## Tests

`tests/gateway/test_kanban_auto_decompose_min_age.py` (not rerun by this mirror pass; the decomposer LLM call is
stubbed): `test_fresh_triage_card_waits_out_the_window_then_is_specified`,
`test_triage_card_scheduled_inside_the_window_stays_scheduled`, `test_window_is_configurable_and_zero_restores_immediate_sweep`,
`test_list_triage_ids_min_age_filter_and_manual_default`, `test_manual_decompose_still_specifies_a_fresh_card`,
`test_window_default_and_tool_text_warn_triage_is_not_a_hold`.

Untested: the `decompose --all` CLI and dashboard button paths (only `list_triage_ids`' default of 0 and
`decompose_task` are exercised), and a live dispatcher loop.

## Applying

Apply 1630 then 1631. New config key `kanban.auto_decompose_min_age_seconds` (default 120).

## Gotchas

- Triage is still not a hold: after the window the card is specified and dispatched. Park it with `kanban_schedule`
  (or create it outside triage) if it must not run.
- The window delays every auto-decomposition by up to 2 minutes plus a tick.

## Plugin?

No. The sweep is part of the gateway's dispatcher tick.
