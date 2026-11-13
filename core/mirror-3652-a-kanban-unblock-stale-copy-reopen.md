# Kanban: unblock of a terminal-held stale copy points at reopen

Patches: **3630, 3631**.

## Behavior
- `unblock` of a blocked/scheduled/triage row whose event log holds the card done or archived is still refused, but the message names the terminal event instead of the misleading "not blocked/scheduled".
- For a held-done copy the message points at `hermes kanban reopen <id> --reason <why>`; for an archived one it says nothing revives it.
- `reopen` now also revives a blocked/scheduled/triage row held done (re-gated landing status, descendant invalidation, a `reopened` event with `previous_status: done` plus `stale_row_status`). A live row is still refused, and reopen never moves a running card.
- The CLI, the operator-call refusal and the dashboard (single PATCH and bulk drag) show the same text, falling back to the old text when no hold applies.

## How it works
- 3630 adds `stale_hold` and `unblock_refusal` to `kanban_terminal_guard`, updates `reopen_done_task`, and un-walruses `unblock_task` without changing evaluation order.
- 3631 re-exports `unblock_refusal` from `kanban_db` so the dashboard avoids a new core boundary import, and pins the reopen UPDATE to `status IN ('done','blocked','scheduled','triage')`.

## Code
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_operator.py`
- `hermes_cli/kanban_operator_call.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_terminal_guard.py`
- `plugins/kanban/dashboard/plugin_api.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`: `test_cli_unblock_of_a_stale_copy_points_at_reopen_and_reopen_revives_it`, `test_cli_unblock_of_a_card_that_is_not_held_keeps_the_generic_message`, `test_unblock_refusal_of_an_archived_copy_names_no_reopen_and_the_dashboard_echoes_it`.
- 3631 adds no test files; it fixes two existing guard tests (boundary lint, source receipt sites) by code change.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The bulk dashboard path is covered only through the archived-copy dashboard echo test; other dashboard paths are untested here.
