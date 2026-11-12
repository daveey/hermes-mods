# Test stability fix: update fixture git never forks a detached repack

Patches: **2699**.

## Behavior

Test-only: no runtime behavior change. One entry removes a CI flake:

- 2699: `test_concurrent_launches_take_turns_and_all_rerun_from_the_restored_tree` (update, interrupted pull) failed in setup, when `git clone` of the fixture origin died with "unable to read sha1 file". On a runner with a recent git (2.54 or later), auto-maintenance defaults to the geometric strategy: once 100+ loose objects exist, `git commit` forks a detached `maintenance run --auto` that packs the loose objects and deletes them. The test clones the origin immediately, and a local clone copies `objects/` directory by directory, so a pack written after the clone passed `pack/`, plus loose files deleted before it reached their directory, left the clone without the second commit's blobs. A throttled runner widens that window. The file's `_git` helper and its one bare `git merge` now carry the updater's own `GIT_NO_DETACH` flags (`gc.autoDetach=false`, `maintenance.auto=false`), so fixture git never runs background maintenance.

## How it works

Test changes only. The test module imports `GIT_NO_DETACH` from `hermes_cli.update_custody` (already defined by the updater) and puts it between `git` and `-C <root>` in the `_git` helper and in the direct `merge origin/main` call of `test_restore_never_touches_user_work_when_git_wrote_nothing`. No production module is touched.

## Code

`tests/hermes_cli/test_update_interrupted_pull.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_update_interrupted_pull.py`. Entry path: fixture git repositories in a temp directory driven through `update_cmd` and `_early_recovery`. The commit message reports a repro with a forced detached repack and a starved clone (10 of 60 clones failed without the flags, 0 of 60 with them); that repro was not run in this mirror.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Only the fixture is protected; the updater's real repositories are unchanged.
- The flake needs git 2.54 or later (or `gc.auto` forced low) to appear; older git rarely triggers it.

## Plugin

Core change; no plugin required.
