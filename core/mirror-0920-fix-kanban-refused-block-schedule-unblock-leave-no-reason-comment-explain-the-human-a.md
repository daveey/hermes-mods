# fix(kanban): refused block/schedule/unblock leave no reason comment; explain the human-answer refusal; reject free text as task ids [daveey]

Patch 0920, source `1184aefb321a5c01177eb3e8212f2f140f24562e`.

## Behavior

fix(kanban): refused block/schedule/unblock leave no reason comment; explain the human-answer refusal; reject free text as task ids [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `from hermes_cli.kanban_output import (`
- `def _bulk_ids(args: argparse.Namespace) -> list[str]:`
- `def _cmd_reopen(args: argparse.Namespace) -> int:`
- `def _cmd_block(args: argparse.Namespace) -> int:`
- `def _cmd_schedule(args: argparse.Namespace) -> int:`
- `+"""CLI helpers for per-task park/unblock ops, split out of hermes_cli/kanban.py. [daveey]`
- `def block_task(`
- `def _landing_status_after_parents(conn: sqlite3.Connection, task_id: str) -> str`
- `def unblock_task(conn: sqlite3.Connection, task_id: str) -> bool:`
- `def delete_task(conn: sqlite3.Connection, task_id: str) -> bool:`
- `def current_run_started_ats(conn: sqlite3.Connection, task_ids: Iterable[str]) -`
- `def schedule_task(`
- `+"""Reason comments that only exist when their state change does, and why a park was refused. [daveey]`
- `+"""A refused kanban op leaves no reason comment, says why, and free text is never parsed as ids. [daveey]`
- `from hermes_cli.goals import handoff_judge_goal, judge_goal`
- `def _handle_schedule(args: dict, **kw) -> str:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> - The PREFIX: reason comment is written by the op itself, inside its write txn and
>   before its transition event, and rolled back to a savepoint unless the op lands
>   (hermes_cli/kanban_reason_comment.py). A refused op leaves no comment; a crash
>   rolls back both together.
> - schedule refused because the card waits on a human answer now says so (block_kind,
>   blocked time) and gives the way out (unblock --reason, then schedule), on the CLI
>   and the kanban_schedule tool.
> - unblock/block/schedule (and every task_ids command) reject tokens that are not
>   task ids, and unknown ids, with exit 2 before mutating anything.
>
> Card t_01a18653b77a4f83.

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_cli_park.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_schedule.py`
- `hermes_cli/kanban_reason_comment.py`
- `tools/kanban_tools.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_refused_op_comments.py`

New or changed test definitions present in the source diff:

- `test_refused_schedule_leaves_no_comment_and_says_why`
- `test_refused_unblock_leaves_no_comment`
- `test_successful_schedule_and_unblock_write_exactly_one_comment_each`
- `test_successful_block_writes_one_comment_before_its_event`
- `test_refused_block_leaves_no_comment`
- `test_unblock_free_text_is_rejected_before_any_change`
- `test_unknown_id_is_rejected_before_any_change`
- `test_schedule_ids_free_text_is_rejected_before_any_change`
- `test_db_comment_rolls_back_with_a_raising_op`
- `test_tool_refused_schedule_leaves_no_comment_and_says_why`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
