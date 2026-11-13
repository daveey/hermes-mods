# Kanban scratch archive: offline, partial-clone aware

Patches: **3565**.

## Behavior
- Archiving an aged blobless (`--filter=blob:none`) or `--no-checkout` clone no longer fails with a credential prompt or a promisor-fetch error; it works without credentials and never touches the network.
- Paths whose HEAD-side blob is not local are recorded in `remote_base.json` (status, mode, oid, path); the worktree content at those paths goes into `remote_base.tar`. The binary diff covers only the remaining paths.
- A missing object in a full (non-partial) clone still refuses. A complete blobless clone archives like a full one. An unborn partial clone archives its files.

## How it works
- 3565 runs every archive git call with `GIT_NO_LAZY_FETCH=1` plus `protocol.allow=never` in the hardened config block (covers older git that ignores the env var). For partial clones with a HEAD it lists changes with `git diff --raw`, finds missing objects with `rev-list --objects --missing=print --no-walk HEAD`, and diffs the kept paths in argv batches with literal pathspecs. Unpushed commits still go to the verified bundle.

## Code
- `hermes_cli/kanban_scratch_archive.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_scratch_archive_partial_clone.py`: `test_no_checkout_blobless_clone_is_archived_without_credentials`, `test_missing_base_keeps_worktree_side_diffs_the_rest_and_never_fetches`, `test_kept_paths_diff_in_batches_with_literal_pathspecs`, `test_complete_blobless_clone_archives_like_a_full_one`, `test_missing_object_in_a_full_clone_still_refuses`, `test_unborn_partial_clone_archives_its_files`, `test_archive_git_never_reaches_the_network`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A missing base blob in a promisor repo is treated as held by the remote, so that side of the change is not archived locally.
