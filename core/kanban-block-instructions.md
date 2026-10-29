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

Standalone: it touches one schema constant and adds one test. Verified: the full 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126, 0127, 0128, 0129, 0130 series applies with
`git am -3 --empty=keep` at merge-base `4957bbd0a0` (Oct 18, 2026 PT).

## Gotchas

- Already-running workers keep the schema they started with; new runs pick it up after `hermes update`.
- blocked-panel splits numbered steps written either one per line or inline as `1) … 2) …` (a run counting up from 0
  or 1). Other formats show as plain text.
