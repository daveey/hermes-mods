# Kanban CLI refuses lifecycle writes under another machine's live lease

Patches: **3530**.

## Behavior
- A CLI lifecycle verb (complete, block, schedule, unblock, request-review, request-changes, reopen-review, reclaim, move, reassign, promote, reopen, and `edit` with `--result`) on a card running under another machine's live Board lease exits non-zero and writes nothing.
- The message is shared by CLI and tools: `cannot <verb> <id>: it is running under <holder>'s live lease. Nothing changed. Let <holder>'s run finish, or archive it to stop it.`
- A bulk command that includes one remote-leased id writes none of the ids.
- `--force` does not bypass the refusal.
- Lifecycle tools return the typed error (`rejection: live_claim`, `code: remote_live_lease`, `holder`); `kanban_move` joins the fenced tools.
- An expired lease, a lease held by this gateway, and a title edit under a remote lease behave as before.
- Archive's stop-request route is unchanged (untested here).

## How it works
Previously `complete` only refused replica copies and local live claims. A remote Board lease is mirrored without a local claim lock, so the CLI wrote `done`, closed the run and appended `completed` before the Board rejected the status. 3530 adds `kanban_remote_stop.cli_refusal(action, args)`, called from `kanban_command` before the handler, using `remote_holder()`. It factors the message into `kanban_remote_stop.refusal` and wires `kanban_move` into the tool-side fence in `tools/kanban_tools.py`.

## Code
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_remote_stop.py`
- `tools/kanban_tools.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_remote_lease_status_writes.py`: `test_cli_complete_under_a_remote_lease_is_refused_and_writes_nothing`, `test_every_cli_status_verb_under_a_remote_lease_is_refused`, `test_cli_edit_of_title_under_a_remote_lease_still_works`, `test_a_bulk_cli_write_with_one_remote_leased_card_writes_none`, `test_every_lifecycle_tool_under_a_remote_lease_is_refused`, `test_cli_complete_after_the_remote_lease_expired_completes_as_before`, `test_cli_complete_when_this_gateway_holds_the_lease_completes_as_before`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Dashboard status writes under a remote lease and repair of already-damaged Board run rows were left as follow-ups; this patch does not cover them.
