# Recover a work tree whose core.bare was flipped

Patches: 0667, fix-up 0668 (core `2aa3397018`, `047865a80e`).

## Behavior

`hermes update` no longer dies when the checkout it is standing in still has a work tree but
`git rev-parse --is-bare-repository` says `true`. It resets `core.bare=false`, prints a WARNING,
and continues. A genuine bare repo (the directory has no `.git` child) is left alone.

The same reset runs before the first `git status` inside the autostash step, so a flipped flag
cannot abort the update with `fatal: this operation must be run in a work tree`.

## Why

On git 2.55, `git init --bare` with no path writes `core.bare=true` when the cwd is the repo's
`.git` or a worktree gitdir under it (`<repo>/.git/worktrees/<name>`), or when `GIT_DIR` names
that directory. `git init --bare <path>` does not. A checkout flipped that way cannot
`git status`. The updater treated that as fatal instead of recovering a directory that still
has a work tree.

## How it works

`recover_flipped_bare_checkout(git_cmd, cwd)` returns immediately when `cwd / ".git"` does not
exist. Otherwise it probes `--is-bare-repository`. On `true`, it resets `core.bare=false`
through `--git-dir`. A `.git` file (linked worktree) is followed via its `gitdir:` line; a file
with no such line is logged and left alone. A failed reset is logged and returns false. A
successful reset logs and prints the WARNING and returns true.

Call sites: the start of `_cmd_update_impl`, and `_stash_local_changes_if_needed` before
`git status --porcelain -z`.

## Code

- `hermes_cli/update_cmd_stash.py`: `recover_flipped_bare_checkout`
- `hermes_cli/update_cmd.py`: call at the start of the git step

## Tests

`tests/hermes_cli/test_update_bare_checkout_guard.py` (4 tests):

- `test_stash_recovers_a_flipped_work_tree_and_warns`: a dirty repo flipped by `git init --bare`
  in its `.git` cannot `git status`; the stash step resets `core.bare`, warns, and status works again.
- `test_recover_is_a_noop_when_already_non_bare`: a normal checkout is not touched.
- `test_recover_leaves_a_genuine_bare_repo_alone`: a repo with no `.git` child stays bare.
- `test_recover_resets_via_worktree_gitdir_flip`: flipping the worktree gitdir (not the main `.git`)
  is reset the same way, and the WARNING is printed.

Untested: the `_cmd_update_impl` call site (only the stash call site and the helper are exercised),
a `.git` file with no `gitdir:` line, a failed `git config` reset, and a BOM-prefixed `.git` file
(0668 reads that file as `utf-8-sig`; the tests use a git-written marker with no BOM).

## Applying

`git am -3 core/patches/0667-*.patch` then `0668-*.patch`, after 0001–0666.
`scripts/run_tests.sh tests/hermes_cli/test_update_bare_checkout_guard.py`.

## Gotchas

- A genuine bare repo is not recovered. Only a directory that still has a `.git` child and reports bare.
- The reset uses `git --git-dir <resolved> config core.bare false`. If that write fails, the update
  still proceeds and the next work-tree command fails as before.
- 0668 is a fix-up of 0667, not a separate feature. Take them together.

## Fix-ups

| Patch | Fixes | Goes with |
|---|---|---|
| 0668 | read the worktree `.git` marker as `utf-8-sig` so `scripts/check` does not flag the 0667 read | 0667 |

## plugin?

No. The reset has to run inside the updater before the first work-tree git command. There is no hook there.
