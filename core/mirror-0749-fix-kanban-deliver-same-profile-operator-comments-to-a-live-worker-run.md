# fix(kanban): deliver same-profile operator comments to a live worker run [daveey]

Patch 0749, source `906cc79472704e3b899b325f5fb8c377b62886ba`.

## Behavior

fix(kanban): deliver same-profile operator comments to a live worker run [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _cmd_comment(args: argparse.Namespace) -> int:`
- `def task_graph_context(conn: sqlite3.Connection, task_id: str) -> dict:`
- `def add_comment(conn: sqlite3.Connection, task_id: str, author: str, body: str)`
- `def test_skips_own_authored_comments_without_env_profile(worker_home, monkeypatc`
- `_comment_poll_last_attempt: float = 0.0`
- `def inject_new_comments_from_env(agent: Any) -> bool:`
- `def _handle_show(args: dict, **kw) -> str:`
- `def _handle_comment(args: dict, **kw) -> str:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The mid-run comment bridge skipped every comment whose author equalled the
> worker's profile. Workers and the chats steering them all run as `default`,
> so every operator note from a same-profile chat was dropped (a MacBook
> worker restarted 16 CPU burners past two "stop" comments).
>
> - add_comment records the writer run on its `commented` event
>   (comment_id + writer_run_id) when HERMES_KANBAN_TASK/RUN_ID name the
>   task, so the tool, `hermes kanban comment` from the worker's shell and
>   delegate children all mark their notes as the run's own, cross-process,
>   with no task_comments schema change.
> - The bridge skips only those ids (author filter kept as the run-less
>   fallback), stamps each note "<author> (another session) at HH:MM TZ",
>   and writes a `comment_delivered {comment_ids, run_id}` event.
> - kanban_comment returns live_run (+ a delivery note: next model call);
>   the CLI prints the same; kanban_show lists undelivered_comments posted
>   during the live run.

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_db.py`
- `tools/kanban_tools.py`

## Tests

Source test files changed by this commit:

- `tests/tools/test_kanban_comment_injection.py`

New or changed test definitions present in the source diff:

- `test_same_profile_operator_comment_is_injected`
- `test_own_kanban_comment_tool_note_is_not_injected`
- `test_own_cli_comment_from_separate_process_is_not_injected`
- `test_mixed_batch_injects_only_the_operator_note`
- `test_comment_delivered_event_records_ids_and_run`
- `test_kanban_comment_reports_live_run_for_running_and_ready`
- `test_kanban_show_lists_undelivered_live_run_comments`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
