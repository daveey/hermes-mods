# kanban_block reasons are written as the human's unblock steps

Patch: `patches/0130-feat-kanban-kanban_block-reason-asks-for-numbered-co.patch`.

What you see: workers that block a card write `reason` as instructions for you: the ask comes first, then numbered
steps naming the exact device, app, setting, page or command, then what to do when you're done ("then unblock this
card", "reply with the value"). Stock Hermes asked for "what you need answered or what stopped you, in one or two
sentences", which produced status summaries that buried the ask. The Desktop plugin
[blocked-panel](../desktop-plugins/blocked-panel/) shows these reasons above the managing chat's composer.

## Behavior

- Schema text only, with no runtime change. The `kanban_block` tool description now says `reason` is shown on the
  board and, in Desktop, in a panel above the managing chat's composer as the steps to unblock it.
- The `reason` property description asks for the following: lead with the ask, numbered steps (`1) … 2) …`) for
  `needs_input`/`capability`, the exact device/app/setting/page/command, what to do when done, and the question plus
  options for a decision.
- No config keys.

## How it works

`tools/kanban_tools_schemas.py`: `KANBAN_BLOCK_SCHEMA` description and its `reason` property. The model sees it
whenever the kanban toolset is loaded (workers, and orchestrator chats that block on behalf of a card).

## Code

- `tools/kanban_tools_schemas.py`
- `tests/tools/test_kanban_tools.py::test_kanban_block_reason_asks_for_unblock_steps`

## Tests

`tests/tools/test_kanban_tools.py::test_kanban_block_reason_asks_for_unblock_steps` (fails on the base schema, passes
with the patch, Oct 18, 2026 PT, `scripts/run_tests.sh`). It asserts the reason description asks for unblock
instructions, ask-first ordering, numbered steps and the exact device/app/setting, that the old "one or two
sentences" wording is gone, and that the tool description names the composer panel.

Untested: whether models actually follow the new wording. That is a prompt-quality question, and no test covers it.

## Applying

Standalone: it touches one schema constant and adds one test. Verified: the full 0001–0130 series applies with
`git am -3` at merge-base `4957bbd0a0` (Oct 18, 2026 PT).

## Gotchas

- Already-running workers keep the schema they started with; new runs pick it up after `hermes update`.
- blocked-panel splits numbered steps written either one per line or inline as `1) … 2) …` (a run counting up from 0
  or 1). Other formats show as plain text.
