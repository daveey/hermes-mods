# feat(kanban): edit --max-retries/--goal-mode/--goal-max-turns/--max-runtime-seconds/--session-id and reopen <done> [daveey]

Patch 0892, source `c350e23068d28528a49e33dd30402aa160913f25`.

## Behavior

feat(kanban): edit --max-retries/--goal-mode/--goal-max-turns/--max-runtime-seconds/--session-id and reopen <done> [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `COMMAND_REGISTRY: list[CommandDef] = [`
- `from hermes_cli import kanban_db_connect as kbc`
- `_DELEGATED_CHILD_DENIED_ACTIONS: frozenset[str] = frozenset({`
- `def _cmd_complete(args: argparse.Namespace) -> int:`
- `def _cmd_edit(args: argparse.Namespace) -> int:`
- `_HANDLERS = {`
- `+"""Operator edits of a Kanban card: the retry/goal/runtime/session columns`
- `_TASK_DICT_FIELDS = (`
- `_SPECS = [`
- `+"""hermes kanban edit operator flags and hermes kanban reopen: the supported`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Operator board writes that skills did with raw sqlite3 (now refused by
> sqlite-live-guard) get supported verbs:
>
> - hermes kanban edit <id> gains --max-retries N, --goal-mode on|off,
>   --goal-max-turns N, --max-runtime-seconds N and --session-id SID (refuses
>   to overwrite a non-empty session_id without --force). Writes go through
>   kanban_db_operator.edit_operator_fields (new sibling module, keeps kanban_db.py under its health cap), which appends one 'edited' event listing
>   old -> new per field; a refusal applies nothing.
> - hermes kanban reopen <id> --reason TEXT: done -> ready (todo while a
>   parent is open), completed_at cleared, breaker reset, 'reopened' event
>   with reason + actor, REOPENED comment; descendants are invalidated via
>   invalidate_descendants_for_parent_reopen like the dashboard reopen.
>   Refuses any other status.
> - show --json now includes goal_mode and goal_max_turns.
>
> Kanban card t_01a185886ffb611e.

## Code

- `hermes_cli/commands.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_db_operator.py`
- `hermes_cli/kanban_output.py`
- `hermes_cli/kanban_parser.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_edit_operator_fields.py`

New or changed test definitions present in the source diff:

- `test_edit_max_retries_reads_back_in_show_json`
- `test_edit_goal_mode_on_and_off`
- `test_edit_goal_max_turns`
- `test_edit_max_runtime_seconds`
- `test_edit_session_id_backfills_empty`
- `test_edit_session_id_force_overwrites`
- `test_edit_operator_and_classic_fields_together`
- `test_edit_same_value_appends_no_event`
- `test_edit_session_id_refuses_overwrite_without_force`
- `test_edit_refuses_non_positive_counts`
- `test_edit_refuses_unknown_task`
- `test_edit_refuses_force_without_session_id`
- `test_edit_goal_mode_rejects_other_values`
- `test_edit_db_function_refuses_blank_session_id`
- `test_reopen_done_task_goes_ready_with_event`
- `test_reopen_invalidates_done_descendants`
- `test_reopen_refuses_non_done`
- `test_reopen_refuses_unknown_task`
- `test_reopen_requires_reason`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
