# Scratch archive restore: tolerate a deleted remote branch

Patches: **3694**.

## Behavior
- Restoring a scratch archive no longer aborts when a tracked remote branch was deleted (and garbage-collected) on the remote after archiving.
- Such a `refs/remotes/*` ref is dropped and reported in the returned manifest as `dropped_remote_refs` (`{git_dir: {ref: oid}}`).
- HEAD, `refs/heads/*`, tags, `refs/stash` and any other unresolved ref still refuse, naming the refs. A local branch needing the deleted tip's commits still refuses loudly.
- A network/auth failure (not a "missing object" refusal) still raises.

## How it works
3694: `_fetch_tips` fetches all missing tips in one call first; on refusal it fetches each tip alone. Only refusals naming the object as gone (`not our ref`, `unadvertised object`, `no such remote ref`, `couldn't find remote ref`) mark a tip gone. `_check_refs` checks each ref by name plus a detached HEAD, deleting unserved remote refs with `update-ref --no-deref -d`; `fsck --connectivity-only` runs as before. `RESTORE_STEPS` and the module docstring describe the drop. Packing objects reachable only from non-default remote branches at archive time was deliberately not done (would undo the archive size saving).

## Code
- `hermes_cli/kanban_scratch_archive_omit.py`
- `tests/hermes_cli/test_kanban_scratch_gc_archive.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_scratch_gc_archive.py`: `test_restore_drops_a_tracked_branch_the_remote_deleted_after_archiving`, `test_restore_still_refuses_when_a_local_branch_needs_the_deleted_tip`, `test_restore_failure_that_is_not_a_missing_object_still_raises`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Dropped remote refs lose their history if the remote no longer serves it; only the report records them.
