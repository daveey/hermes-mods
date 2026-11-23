# Kanban: origin chat comments routed to their bound owner (mirror 5707–5710)

## Behavior
- `kanban_comment` from an origin chat can reach a card whose owner lives elsewhere via a comment-only courier route, scoped to a validated unreleased local origin binding and the chat's continuation. It grants no unblock or human-answer authority.
- Identical text on the same owner ask reuses one durable transport request, and keeps its id across session compression; queued/unknown and unconfirmed outcomes are distinguished from owner-confirmed application; oversized payloads are rejected before truncation.
- The courier holds a drain lease (inventoried spawn) and honors the opened board DB pin / canonical current board.

## How it works
`kanban_origin_comment.py` with a hook in `tools/kanban_tools.py`; `worker_drain_spawn_inventory.json` lists the spawn.

## Code
- `hermes_cli/kanban_origin_comment.py`
- `tools/kanban_tools.py`
- `hermes_cli/worker_drain_spawn_inventory.json`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_kanban_origin_comment.py::test_origin_tool_comment_reaches_owner_only_as_agent` calls the tool route with simulated courier transport and real owner SQLite, preserving running status. `test_courier_holds_real_drain_lease_and_refuses_closed_latch` checks the fence. `test_kanban_origin_comment_retry.py::test_retry_after_real_compression_reuses_request` checks stable identity; `test_kanban_origin_comment_db_pin.py::test_omitted_board_honors_db_pin` checks selection. A live remote courier is untested.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Unrelated chats and workers keep their existing paths.

## plugin?
No. Core change.
