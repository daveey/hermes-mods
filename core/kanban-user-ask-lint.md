# A block on a human must say what unblock and archive mean

Patches:

- `patches/0986-fix-kanban-a-block-on-David-must-say-what-unblock-an.patch`

Asks like "Pick A, B or C" left the card showing only "blocked" with nothing to act on.

## Behavior

`kanban_block` and `kanban_create` (tool entry points) refuse a `human_request` whose `action` does not state what unblocking and what archiving do (rejection `user_ask_incomplete`). Internal trackers (`kb.create_task`) are not linted. The lint runs after the standing-wait and park guards.

## How it works

`kanban_block_policy.py` holds the lint; `kanban_tools.py` calls it.

## Code

`hermes_cli/kanban_block_policy.py`, `tools/kanban_tools.py`.

## Tests

`tests/tools/test_kanban_user_ask_lint.py` (3 failed before, 4 pass after); several fixtures with ask-less actions were updated.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.
