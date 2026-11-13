# Kanban reopen and unblock of a stale terminal-held copy

Patches: **3664, 3665**.

## Behavior
- Reopening a stale copy (a blocked, scheduled or triage row whose card a `completed` event holds done) no longer leaves an open run row behind; the run the row pointed at is ended.
- `hermes kanban reopen` of an archived stale copy now prints the same "nothing revives it" refusal that unblock prints, instead of "status is 'scheduled', not 'done'; use unblock/reopen-review".
- A worker's operator-call unblock of a card held done by its `completed` event is refused with text naming the completed event and `hermes kanban reopen <id> --reason`; the card stays blocked.

## How it works
- 3664 makes the stale-copy branch of `reopen_done_task` call `_reclaim_dangling_run` (same statuses as unblock) inside the transaction before the status UPDATE. `unblock_refusal` takes `verb="reopen"` (archived holds only; reopen accepts a done hold) and the CLI reopen command prints it.
- 3665 adds only a test pinning the operator-call unblock refusal text (a blocked row plus a later `completed` event; a scheduled copy is refused earlier as "not blocked").

## Code
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_db_operator.py`
- `hermes_cli/kanban_terminal_guard.py`
- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`
- `tests/hermes_cli/test_kanban_operator_call.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`: `test_reopen_of_a_stale_copy_with_a_live_run_pointer_ends_that_run`, `test_cli_reopen_of_an_archived_stale_copy_says_nothing_revives_it`.
- `tests/hermes_cli/test_kanban_operator_call.py`: `test_unblock_of_a_terminal_held_stale_copy_names_the_completion_and_reopen`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The dashboard reopen path is not covered by a test in these patches (untested here).
