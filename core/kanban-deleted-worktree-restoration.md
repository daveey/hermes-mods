# Deleted worktree restoration

Patches 2068.

## Behavior

Restore a deleted concrete worktree on the card’s existing branch instead of treating a sibling-layout path as invalid configuration. A dispatcher restoration records `worktree_restored` with path, repository, branch and timestamp.

## How it works

The resolver checks the board default workdir and up to 200 sibling directories, deduplicates Git common directories and requires exactly one repository containing the existing branch. It prunes stale registration and re-adds the worktree without creating a branch.

## Code

`hermes_cli/kanban_db_workspace.py` and `hermes_cli/kanban_db_dispatch.py`.

## Tests

`tests/hermes_cli/test_kanban_worktree_restore.py`: both filesystem deletion and Git worktree removal preserve branch/HEAD after respawn; missing branches produce a deletion diagnostic; an unknown sibling path never creates a guessed branch.

Execution status is recorded in [mirror 2072 verification](mirror-2072-verification.md); listed regression cases alone are not an execution claim.

## Applying

Use the complete snapshot’s [README Applying recipe](README.md#applying), including its replay plan and policy. These patches depend on earlier core changes.

## Gotchas

Ambiguous repositories are not guessed. The change cannot recover uncommitted files or a deleted branch. A restore event is not a guarantee that later cleanup will preserve the directory.

## Could this be a plugin?

No standalone plugin is supplied: this changes core dispatch/admission or turn-finalization wiring.
