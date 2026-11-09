# Kanban scratch GC: explain preserved scratch without expanding deletion authority

Patches **2033** (`f0c1f272ab1bec8e8c31eb766959685602cc30fc`, `Explain preserved scratch without expanding GC authority`) and **2034** (`92c0b758149b868b98154fec174f9423721676d0`, `Isolate scratch inventory failures and recognize nested owners`). Related: [kanban-scratch-git-preservation.md](kanban-scratch-git-preservation.md), [kanban-scratch-archive.md](kanban-scratch-archive.md).

## Behavior

`gc_task_scratch` now logs why scratch survives a pass, but its deletion candidates are unchanged (terminal-status scratch tasks on this board):

- **`reason=owner_unverified`**: a directory directly under the workspaces root that no local task record (this board or any sibling board) claims. Logged with `no deletion deadline`; the directory is never deleted. A sync-shadow database row for the same name is not ownership evidence.
- **`reason=retention_pending`** (`older_than_days > 0`) or **`reason=retention_disabled`** (`<= 0`): a known terminal task's scratch kept because it is not yet removable and not old enough. The line names the path, task and `eligible_at` (completion time + days), noting that the deadline is only archival eligibility and all safety guards still apply.
- Logging retention writes no task events, so observing retention never extends its age; repeated passes keep the same `eligible_at`.
- Symlinks and plain files under the root are neither followed nor reported.
- A running task's scratch, a task whose `workspace_path` is NULL (implied `root/<task id>`), a sibling-board record and a record pointing at a **nested** path inside a root child (2034) all count as owners.
- An unreadable board still aborts the whole pass (`cannot read all boards; preserving workspaces`) without claiming unknown owners.
- 2034: if only the root inventory listing fails (`OSError`), GC logs `Scratch preservation inventory unavailable; unknown paths remain preserved` and still cleans up known eligible scratch.

## How it works

`hermes_cli/kanban_worktree_gc.py`:

- `_scratch_owner_paths(conn)` reads `id, workspace_kind, workspace_path` from every task of a board and returns resolved paths (explicit path, or `workspaces_root(board)/<id>` for scratch with no path). It feeds only the report, never the deletion loop.
- The previous sibling-board probe (`SELECT workspace_path FROM tasks`) now collects those paths, inside the same `try` that aborts on `OSError`/`sqlite3.Error`.
- `_report_unowned_scratch(root, known_paths)` iterates root children only. 2034 maps each known path to the root child that contains it (`relative_to(root).parts[0]`), so nested records represent their top-level directory, and moves the call into its own `try/except OSError` after the board reads.
- `_report_scratch_retention(row, path, days)` is called where the loop previously did a silent `continue` for not-removable, not-yet-old scratch. It uses `completed_at`, else `last_event`, else `created_at`.

## Code

- `hermes_cli/kanban_worktree_gc.py`: `gc_task_scratch`, `_scratch_owner_paths`, `_report_unowned_scratch`, `_report_scratch_retention`.

## Tests

`tests/hermes_cli/test_kanban_scratch_preservation.py` (new in 2033, extended in 2034; fixtures from `tests/hermes_cli/test_kanban_scratch_gc_archive.py`):

- `test_moved_owner_absent_locally_is_preserved`
- `test_known_clone_reports_stable_retention` (7 days / 0 days)
- `test_actual_nonterminal_owner_is_not_unknown`
- `test_sibling_record_and_null_path_owner_are_known`
- `test_inventory_does_not_follow_symlinks_or_classify_files`
- `test_unreadable_board_aborts_without_unknown_owner_claim`
- `test_nested_record_represents_root_child` (2034)
- `test_inventory_failure_does_not_block_known_cleanup` (2034)
- `test_completed_time_wins_over_later_diagnostics`

```sh
scripts/run_tests.sh tests/hermes_cli/test_kanban_scratch_preservation.py tests/hermes_cli/test_kanban_scratch_gc_archive.py
```

Not rerun in this mirror pass; listed from the patches.

## Applying

Apply 2033 and 2034 together, in order, through the snapshot-bound replay in [README.md](README.md). 2033 alone lets an inventory `OSError` abort the whole GC pass and reports nested-path owners as `owner_unverified`.

## Gotchas

- These are WARNING log lines, not task events or UI. Grep the gateway/CLI log for `Scratch preserved:`.
- `owner_unverified` does not guess task IDs from directory names, and nothing in this change deletes such directories. Reclaiming them is a manual or separate decision.
- Only root children are inventoried. Ownership of deeper directories is not reported.
- `eligible_at` is `None` when retention is disabled or the task has no timestamp.

## Plugin?

No. Scratch GC runs inside core Kanban maintenance with direct board access.
