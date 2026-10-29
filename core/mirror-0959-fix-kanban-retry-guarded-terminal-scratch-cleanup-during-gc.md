# fix(kanban): retry guarded terminal scratch cleanup during GC [daveey]

Patch 0959, source `4c2a3c5e25244c2b173256d735841291a86fbbcd`.

## Behavior

fix(kanban): retry guarded terminal scratch cleanup during GC [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _conn_uses_path(`
- `from pathlib import Path`
- `def _cmd_watch(args: argparse.Namespace) -> int:`
- `def _cmd_gc(args: argparse.Namespace) -> int:`
- `def retention_days() -> int:`
- `+"""GC retries deferred terminal scratch cleanup without guessing ownership."""`

## Code

- `hermes_cli/kanban_db_workspace.py`
- `hermes_cli/kanban_ops.py`
- `hermes_cli/kanban_worktree_gc.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_scratch_gc.py`

New or changed test definitions present in the source diff:

- `test_gc_retries_terminal_scratch`
- `test_gc_keeps_nonterminal_and_unknown_scratch`
- `test_gc_preserves_uncertain_scratch`
- `test_gc_retries_after_cross_board_sharer_finishes`
- `test_gc_preserves_parent_with_active_child`
- `test_gc_reads_all_boards_before_removing_any_scratch`
- `test_gc_keeps_scratch_when_board_discovery_fails`
- `test_cleanup_protects_overlapping_live_workspaces`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
