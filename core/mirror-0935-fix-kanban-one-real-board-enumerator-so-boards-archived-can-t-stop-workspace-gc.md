# fix(kanban): one real-board enumerator so boards/_archived can't stop workspace GC [daveey]

Patch 0935, source `15c424d93d0f15fcef1e314a43e3f1f354d1c0e6`.

## Behavior

fix(kanban): one real-board enumerator so boards/_archived can't stop workspace GC [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _dir_holds_board(d: Path) -> bool:`
- `def _sibling_board_db_files(conn: sqlite3.Connection) -> list[Path]:`
- `def _workspace_in_use_by_other(`
- `+"""One Kanban card when workspace GC can't read a board. [daveey]`
- `class Hub:`
- `def sweep_repo(repo_root: str) -> None:`
- `def _sweep_repo(repo_root: str) -> None:`
- `def test_terminal_scratch_parent_still_swept_after_last_child(`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> An empty boards/_archived/kanban.db (made by a stray 'sqlite3 boards/*/kanban.db'
> glob) was read as a board by _sibling_board_db_files(): every workspace cleanup
> was deferred as 'unknown' and the startup worktree sweep aborted with
> 'no such table: tasks' (kanban-e808885f9e).
>
> - kanban_db.named_board_db_files(): the one enumerator of boards/<slug>/kanban.db,
>   slug rule = _BOARD_SLUG_RE (same as list_boards). Used by the GC/sharing scan
>   and the sync client's board_paths(). A real slug board that can't be read
>   still fails closed.
> - Detection: kanban_gc_alert opens ONE card (idempotency_key
>   kanban-worktree-gc-abort) naming the db path when the sweep aborts on a board
>   or a cleanup is deferred as unknown; repeats comment at most hourly per path;
>   a recurrence after the card is done opens a fresh card.

## Code

- `hermes_cli/kanban_db_boards.py`
- `hermes_cli/kanban_db_workspace.py`
- `hermes_cli/kanban_gc_alert.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_worktree_gc.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_worktree_teardown.py`

New or changed test definitions present in the source diff:

- `test_named_board_files_skip_non_slug_folders`
- `test_startup_sweep_ignores_non_board_folders`
- `test_workspace_sharing_ignores_non_board_folders`
- `test_workspace_sharing_real_board_without_tables_fails_closed`
- `test_startup_real_board_without_tables_aborts_and_opens_one_card`
- `test_unknown_cleanup_deferral_opens_one_card`
- `test_gc_abort_card_comments_once_then_reopens_after_done`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
