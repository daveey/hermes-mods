# Kanban rejects worktree workspaces that can never resolve, at create time

Patch: `patches/0122-fix-kanban-reject-unresolvable-worktree-workspaces-a.patch`.

What you see: an agent (or you) creates a card with `workspace_kind=worktree` and a path the dispatcher could never
turn into a worktree, such as `~/code/<repo>-<slug>` next to the repo rather than inside it. Stock Hermes accepts
the card; the dispatcher then fails the same way on every spawn, spends the card's retry budget
(`spawn_failed` → `gave_up`) and the creator never learns why. With this patch the create call fails immediately
with an error that names the fix, and a card that slipped in anyway (older rows, internal callers) blocks on its
first spawn failure with the same reason.

## Behavior

- **`kanban_create` tool and `hermes kanban create --workspace worktree:<path>`** validate a non-project worktree
  workspace before inserting anything. Accepted, with the same rules the spawn-time resolver uses:
  - an existing linked worktree checkout;
  - a path inside a git repo, including a repo root (the dispatcher then creates `<repo>/.worktrees/<task-id>`);
  - a not-yet-existing path whose parent is inside a repo;
  - no path at all, when the board's `default_workdir` is an absolute path inside a git repo.
- Rejected, with no row written:
  - a relative path ("is not absolute; use an absolute path");
  - no path and a board with no `default_workdir`, or one that is relative / not in a repo;
  - a path outside any repo. The message says `Fix: use workspace_path='<repo>'` or
    `git -C <repo> worktree add <path> -b <branch>`. For a `<repo>-<slug>` name it suggests the sibling repo
    (longest matching sibling directory that is a repo root); otherwise `<repo root>`.
- The tool returns `{"error": ...}` to the creating agent; the CLI prints `kanban: --workspace: <reason>` to stderr
  and exits 2.
- **Dispatcher**: when spawn-time resolution raises the new `WorktreeWorkspaceConfigError`, the card is blocked on
  the FIRST failure (sticky `gave_up`, payload `workspace_config_error: true`, `last_failure_error` = the reason)
  instead of retrying. `recompute_ready` does not auto-resume it. Other workspace errors (I/O and the like) still
  retry as before.
- Project-linked cards (`project_id` set) are not validated here; their worktree comes from the project.
- The `kanban_create` schema's `workspace_path` description now states the accepted shapes.

## How it works

- `hermes_cli/kanban_db_workspace.py`: new `WorktreeWorkspaceConfigError(ValueError)`. `_resolve_worktree_workspace`
  raises it (instead of a plain `ValueError`) for the deterministic cases: relative path, no repo to anchor on,
  missing/invalid board `default_workdir`. New `worktree_workspace_problem(workspace_path, board=)` applies the
  resolver's acceptance rules without creating anything and returns the reason string or `None`.
  `_sibling_repo_hint` finds the `<repo>` for a `<repo>-<slug>` path. The resolver's own "not inside a git repo"
  error reuses the same message so spawn-time and create-time text match.
- `hermes_cli/kanban_db.py`: `create_task(..., validate_workspace=False)`. When True and the card is a non-project
  worktree, it calls `worktree_workspace_problem` before the insert and raises on a problem. The default keeps
  every internal caller (decompose, imports, tests) unchanged.
- `tools/kanban_tools.py` `_handle_create` and `hermes_cli/kanban.py` `_cmd_create` pass `validate_workspace=True`;
  the CLI catches the error and exits 2.
- `hermes_cli/kanban_db_dispatch.py` `_dispatch_lane_task`: on a spawn exception, `force_trip=True` and the extra
  event payload when the exception is a `WorktreeWorkspaceConfigError`.

## Code

- `hermes_cli/kanban_db_workspace.py`: `WorktreeWorkspaceConfigError`, `worktree_workspace_problem`,
  `_sibling_repo_hint`, `_resolve_worktree_workspace`.
- `hermes_cli/kanban_db.py`: `create_task(validate_workspace=...)`.
- `hermes_cli/kanban.py`: `_cmd_create`.
- `hermes_cli/kanban_db_dispatch.py`: `_dispatch_lane_task`.
- `tools/kanban_tools.py` (`_handle_create`), `tools/kanban_tools_schemas.py` (description).

## Tests

`tests/hermes_cli/test_kanban_worktree_create_validation.py` (10 cases, all pass Oct 17, 2026 PT,
`scripts/run_tests.sh`):

- `test_create_rejects_sibling_path_outside_repo_and_names_fix`: `create_task(validate_workspace=True)` with
  `<code>/myapp-s4-...` raises, writes no row, and the message suggests `myapp` and the exact `git worktree add`.
- `test_create_accepts_resolvable_worktree_paths[repo_root|inside_repo|linked_worktree]`.
- `test_create_rejects_relative_and_missing_board_default`.
- `test_create_without_validation_keeps_legacy_behaviour`: internal callers are unchanged.
- `test_kanban_create_tool_returns_error_to_creator`: tool entry path (`_handle_create`) returns the error, no row.
- `test_cli_create_rejects_bad_worktree`: CLI entry path (`_cmd_create`) exits 2 with the fix on stderr, no row.
- `test_dispatcher_blocks_config_error_on_first_failure`: dispatcher entry path; one `dispatch_once` blocks the card
  (no spawn, `consecutive_failures == 1`, sticky `gave_up` with `workspace_config_error`), and `recompute_ready`
  leaves it blocked.
- `test_dispatcher_transient_workspace_error_still_retries`: control; an `OSError` leaves it `ready`.

Untested: card creation from the dashboard/Desktop board REST route and `kanban decompose`, which do not pass
`validate_workspace` (they rely on the first-spawn block).

## Applying

Standalone in code: touches the kanban workspace resolver, `create_task`, the CLI and tool create paths and the
dispatcher's spawn-failure handler. A rebase conflict usually lands in `create_task`'s long keyword list or in
`_cmd_create`'s `create_task(...)` call. Verified: applies with `git am -3` on top of 0001–0121 at merge-base
`4957bbd0a0` (Oct 17, 2026 PT).

## Gotchas

- The validation runs `git` against the path (via the resolver's helpers), so creating a worktree card is a few ms
  slower and depends on `git` being on PATH where the create runs.
- Validation happens on the creating host. A path valid there but not on the dispatching host still fails at spawn,
  now as a first-failure block.
- Blocked cards from this check are sticky: fix the workspace (edit the card) and unblock it by hand.

## Plugin?

No. The check has to run inside `create_task` before the insert and in the dispatcher's spawn-failure accounting;
neither has a plugin hook.
