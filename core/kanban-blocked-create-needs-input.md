# Creating a card blocked with a human_request classifies it needs_input

Patch: 0693 (daveey/hermes `d4cb0dafe1`). Related: [kanban-ownerless-blocks.md](kanban-ownerless-blocks.md).

## Behavior

`kanban_create(initial_status="blocked", human_request=...)` used to store the request and a `blocked` event but leave `block_kind` NULL, because only `block_task` classified cards. Now the card gets `block_kind='needs_input'` in the same UPDATE and the `blocked` event carries `kind=needs_input`. A blocked creation without a human request stays unclassified.

## How it works

`record_created` in `hermes_cli/kanban_db_creation.py` sets the kind with the human-request UPDATE. Creation-time asks are decision waits (`validate_human_request` refuses dependency/transient/time kinds alongside one), so `needs_input` is the only consistent kind.

## Code

`hermes_cli/kanban_db_creation.py`.

## Tests

- `tests/hermes_cli/test_kanban_human_request.py`: `test_initial_blocked_with_request_is_classified_needs_input`, `test_initial_blocked_without_request_keeps_kind_unset`. Both call the creation function directly; the `kanban_create` tool and CLI entry paths are untested here. This mirror did not re-run them.

## Applying

`git am -3` 0693 in series order.

## Gotchas

- Cards created blocked before this fix keep NULL `block_kind`; nothing backfills them.

## Plugin?

No.
