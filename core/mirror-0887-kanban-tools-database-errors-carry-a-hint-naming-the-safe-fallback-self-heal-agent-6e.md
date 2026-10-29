# [daveey] kanban tools: database errors carry a hint naming the safe fallback (self-heal agent-6eb3a51e94)

Patch 0887, source `a38da79f8aa3e896eb48e418327fe2ba25049434`.

## Behavior

[daveey] kanban tools: database errors carry a hint naming the safe fallback (self-heal agent-6eb3a51e94). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `+"""A kanban tool whose board raises a SQLite error must name the safe fallback (self-heal agent-6eb3a51e94).`
- `from hermes_cli.config import cfg_get, load_config`
- `def _kanban_handler(tool_name: str, *, classify_rejections: bool = False) -> Cal`
- `+"""Kanban tool error bodies: the safe next step when the board's database fails. [daveey]`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A bare 'kanban_show: disk I/O error' left an agent with no route; it wrote the live
> board with raw sqlite3 and deleted its -shm/.corrupt.*.bak files. sqlite3.DatabaseError
> and KanbanDbCorruptError now return a 'hint': retry once, then 'hermes kanban [--board b]
> <verb>' in a fresh process, never raw writes or deleting board files, 'recover' when
> integrity_check fails, block if the CLI fails too. Other errors are unchanged.

## Code

- `tools/kanban_tools.py`
- `tools/kanban_tools_db_hint.py`

## Tests

Source test files changed by this commit:

- `tests/tools/test_kanban_db_error_hint.py`

New or changed test definitions present in the source diff:

- `test_disk_io_error_on_show_carries_the_cli_fallback_hint`
- `test_disk_io_error_on_comment_names_the_comment_verb`
- `test_database_error_and_corrupt_board_carry_the_hint`
- `test_named_board_hint_puts_the_board_flag_before_the_verb`
- `test_other_errors_get_no_db_hint`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
