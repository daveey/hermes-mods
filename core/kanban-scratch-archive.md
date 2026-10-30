# Archive, then remove, aged terminal Kanban scratch holding git clones

Patch: 1299. Fork commit: `1e40faadc0` (fork PR #331).

## Behavior

Completion cleanup and `gc_task_scratch` keep every scratch tree that holds Git metadata (see `kanban-scratch-git-preservation.md`), so a worker's clone outlived its card forever. Now `gc_task_scratch` (run by `hermes kanban gc`) archives each standalone repo of a terminal scratch workspace whose card finished more than `kanban.worktree_retention_days` ago (default 7; `0` disables it) and then removes the workspace.

The archive lands in `<kanban home>/archive/scratch/<board>/<task>/<relpath>/` with:

- `commits.bundle`: refs and HEAD not reachable from the repo's remote-tracking refs, verified with `git bundle verify` (omitted when there are none);
- `tracked.diff`: `git diff --binary HEAD` (omitted when empty);
- `untracked.tar`: `git ls-files --others --exclude-standard` (omitted when empty; symlinks are recorded, never followed);
- `meta.json`: task id, completed_at, source, HEAD, branch, remotes, unpushed refs, untracked count, file list, archived_at.

A removed workspace appends a `workspace_archived` task event (`path`, `archive`). A workspace that is itself a clone archives into `<task>/` directly; nested clones go under their relative path.

**Kept instead** (nothing is removed; the reason is logged at info level): a `.git` FILE anywhere inside (linked worktree or submodule; the worktree GC owns those), a bare repository, any scan error, any failed archive step (git failure, unwritable archive dir), a non-terminal card, an active child, a live sharer of the path (including on another board), a card younger than the retention, and retention `0`.

## How it works

`gc_task_scratch(conn, *, older_than_days=None)` reads every board's terminal scratch rows first (fail closed on an unreadable board). Trees removable under the old rule are removed as before. A tree kept only because of Git metadata is queued if the card's age (`completed_at`, else last non-workspace event, else `created_at`) passes the retention and no live sharer exists. Queued trees are archived after the board write transaction ends, because git on big clones takes minutes. `hermes_cli/kanban_scratch_archive.py` builds the archive in a `.partial-<pid>` sibling and renames it into place, so a failure leaves no half archive. After archiving, the owner is re-checked under the write lock (still terminal, no active child, no sharer) and the tree is renamed to `.<name>.gc-<pid>` inside that lock, so a resumed card never sees a half-deleted tree; the renamed tree is then removed. `_cmd_gc` passes `worktree_retention_days` through. Git runs hardened (`noninteractive_repo_git_env`) with a 600 s timeout per command.

## Code

- `hermes_cli/kanban_scratch_archive.py` (new): `find_repos`, `archive_repo`, `archive_workspace`, `ArchiveRefused`.
- `hermes_cli/kanban_worktree_gc.py`: `gc_task_scratch`, `_finished_before`, `_board_slug`, `_archive_and_remove_scratch`.
- `hermes_cli/kanban_ops.py`: `_cmd_gc` passes the retention to `gc_task_scratch`.

## Tests

`tests/hermes_cli/test_kanban_scratch_gc_archive.py` (real git, real board DB, driven through `kanban_ops._cmd_gc`): clean pushed clone archived and removed with a `workspace_archived` event; uncommitted and untracked files land in diff and tar (symlink not followed); unpushed commit lands in a verifiable bundle; workspace that is itself a clone; young card kept; retention 0 kept; non-terminal statuses kept; active child kept; live sharer on another board kept; linked worktree inside kept; bare repo inside kept; unwritable archive dir keeps everything; injected failed archive step keeps the tree and leaves no partial; named board archives under its slug. This mirror task did not execute these tests; it only replayed the series with `git am -3`.

## Applying

Apply 1299 after the series, or add `kanban_scratch_archive.py` and the `gc_task_scratch` hook on newer core. No hotkeys. The only config key is the existing `kanban.worktree_retention_days`.

## Gotchas

Archives are never pruned by this feature; they accumulate under `<kanban home>/archive/scratch`. Completion-time cleanup still keeps Git-bearing scratch; only the GC archives, and only after the retention. Restoring is manual: `git clone` the remote, `git fetch commits.bundle`, `git apply tracked.diff`, extract `untracked.tar`. No plugin is involved.
