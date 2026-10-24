# Kanban worktree retention and orphan detection

Patch: `0264-fix-kanban-archive-and-reap-stale-terminal-worktrees.patch`.

## Behavior

`hermes kanban gc --worktree-retention-days N` extends cleanup to done, archived,
failed and cancelled worktree tasks. The config default is
`kanban.worktree_retention_days: 7`. Zero disables age-based archival (legacy
clean/pushed cleanup remains). The age uses persisted task/event timestamps,
not directory mtime.

Once eligible, a clean worktree's committed history is saved to a verified local
`refs/hermes/kanban-archive/<task-id>/<commit>` ref before removal. Generated
`wt/` branches are compare-and-deleted; custom branches survive. Recovery refs
survive ordinary Git GC and have no automatic expiry.

CLI worktree startup maintenance sweeps the current home's boards and the selected
board using the same policy. Missing-owner registered `t_<hex>` trees receive
JSON tombstones under `<hermes-home>/archive/kanban-orphans/`; their contents are
not deleted. This is intentionally conservative because absence of a task row is
not evidence that another owner's files are disposable.

## How it works

A board write transaction prevents the owning terminal task being resumed during
cleanup. Active children, shared live workspace owners and unreadable sibling
boards defer removal. Dirty/untracked work remains in place, and removal never
uses `--force`. Locked worktrees are protected by Git's own removal check. An
archive failure aborts removal. The startup sweep reads every discovered board
before any mutation, so corruption cannot be mistaken for an absent owner.

## Code

- `hermes_cli/kanban_worktree_gc.py`: retention, board sweep and tombstones.
- `hermes_cli/kanban_db_workspace.py`: verified recovery refs and branch pruning.
- `hermes_cli/kanban_ops.py`, `kanban_parser.py`: GC integration and option.
- `hermes_cli/worktree_ops.py`: existing startup maintenance integration.
- `hermes_cli/config_defaults.py`: retention default.
- `website/docs/user-guide/features/kanban.md`: recovery instructions.

## Tests

- `test_kanban_worktree_teardown.py`: 38 passed. Reproduces stale unpushed GC
  retention before the fix, then proves removal and archive recovery after Git GC;
  covers terminal/live statuses, recent/disabled retention, dirty/locked/shared
  trees, active children, archive failure, missing-owner tombstones, unreadable
  boards, and config-driven startup entry.
- `test_kanban_gc_retention.py`: 10 passed (existing event/log retention).
- `test_worktree.py`, `test_worktree_pushed_tier.py`: 44 passed, 6 Linux-only
  tests skipped on macOS. Run from a plain source copy to avoid the test
  real-home guard rejecting linked-worktree metadata inside the runtime home.
- `test_worktree_maintenance_isolation.py`, `test_install_states_gc.py`: 4 passed.
- `python scripts/check`: all 12 checks passed.

## Applying

Apply in series after 0263. Fork commit `ae781e9188`.

## Gotchas

Recovery refs are local, not remote backups. Keep the original repository. List
with `git for-each-ref refs/hermes/kanban-archive/`, then restore with
`git worktree add -b recovered-task ../recovered-task <full-recovery-ref>`.
Deleted-owner trees are only tombstoned; reclaiming those remains manual.
No production backlog was deleted by verification. Already-running processes need
normal core deployment before using the new cleanup policy.

## Plugin?

No: this extends core workspace teardown and the existing GC/startup entry points;
there is no plugin lifecycle hook owning their Git removal policy.
