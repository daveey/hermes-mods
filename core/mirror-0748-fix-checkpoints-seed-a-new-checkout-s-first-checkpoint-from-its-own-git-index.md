# fix(checkpoints): seed a new checkout's first checkpoint from its own git index [daveey]

Patch 0748, source `64d749323e1fbad79d7664ad785cb62ddcad103e`.

## Behavior

fix(checkpoints): seed a new checkout's first checkpoint from its own git index [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `"symbol": "_run_git",`
- `class TestStagingBudget:`
- `def _run_git(`
- `def _git_out(args: list[str], store: Path, working_dir: str, rc: Optional[set[in`
- `class CheckpointManager:`
- `With a container terminal backend (docker, singularity, modal, daytona,`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Follow-up to 72755549d1. That commit prices `git add -A` before running it and skips a
> folder over the staging budget. Its coverage limit: the first snapshot of a big fresh
> checkout has a cold checkpoint index, so every file is priced (and would be hashed and
> written). A hermes-agent worktree (19,009 files, 208 MB) at a load factor of 40 is
> priced at ~1134 s against a 30 s budget, so it got no checkpoint until the host went
> quiet, retried hourly.
>
> The checkout's own index already holds each tracked file's blob id and stat data.
> CheckpointManager._seed_from_repo_index, run when a project has no checkpoint index:
> - only when working_dir is the top level of a git checkout (clone or linked worktree);
> - copies that index (mtime kept), drops the untracked-cache/fsmonitor extensions, and
>   force-removes tracked entries the checkpoint excludes (DEFAULT_EXCLUDES, .gitignore),
>   so the tree equals what a cold add produces;
> - gives up (no index left, the cold path runs as before) on a split index, conflicts,
>   assume-unchanged or skip-worktree entries, which would hide changes from git add;
> - copies the blobs the store lacks from the repository as one pack (pack-objects
>   --window=0 into a temp file in the store, then the store's index-pack), with lazy
>   fetch off, so the checkpoint never depends on the repository's object store and
>   nothing is written into the user's repository. A sibling worktree whose blobs are
>   already in the store copies nothing.
> The estimate for a seeded index uses the cold free estimate and then
> `ls-files -o -m`, so only files new or changed relative to the checkout's index count.
>
> Tests (tests/tools/test_checkpoint_manager.py::TestColdIndexSeeding):
> - test_cold_20k_file_worktree_is_snapshotted_within_budget_under_load: a cold 20,000-file
>   worktree with an edit, a delete and a new file at load_factor 40 is snapshotted inside
>   the budget with one git add; content checked; every object is in the store. Red on main.
> - test_second_worktree_reuses_the_stores_blobs: no second pack. Red on main.
> - test_untracked_heavy_tree_is_still_skipped[no-repo|untracked-in-repo]: control, still
>   skipped with no git add.
> - test_seeded_snapshot_equals_a_cold_one: seeded tree == cold tree of a plain copy
>   (node_modules/, *.pyc, a force-added gitignored file stay out); fails with the
>   exclusion drop disabled.
> - test_assume_unchanged_entry_does_not_hide_an_edit: fails with the flag guard disabled.
> Live (hermes-hub, real manager, cold hermes-agent worktree, load_factor forced to 40):
> origin/main skips it in 2.2 s with no checkpoint; this commit snapshots it in 5.7 s.
>
> Undo: git revert this commit.
> Hub card t_01a1827b171dd664.

## Code

- `tools/checkpoint_manager.py`
- `website/docs/user-guide/checkpoints-and-rollback.md`

## Tests

Source test files changed by this commit:

- `tests/fixtures/resolution_allowlist.json`
- `tests/tools/test_checkpoint_manager.py`

New or changed test definitions present in the source diff:

- `test_cold_20k_file_worktree_is_snapshotted_within_budget_under_load`
- `test_untracked_heavy_tree_is_still_skipped`
- `test_seeded_snapshot_equals_a_cold_one`
- `test_assume_unchanged_entry_does_not_hide_an_edit`
- `test_second_worktree_reuses_the_stores_blobs`
- `test_slow_dir_backoff_file_with_bom_is_read`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
