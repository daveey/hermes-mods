# Preserve Git-bearing Kanban scratch workspaces

Patch: 0269. Fork commit: `20358a17bdec17ed5692d9be0f4b99b60c3c4ddf`.

## Behavior

Successful tool, CLI and REST completion no longer recursively deletes scratch containing Git repositories or linked worktrees. The whole scratch directory is retained when any `.git` entry, bare-repository shape, symlink or scan uncertainty is encountered. This applies to direct cleanup and deferred parent cleanup. Rejected completion still leaves all data untouched. Ordinary non-Git scratch retains its existing cleanup behavior.

## How it works

`_scratch_tree_removable` walks without following symlinks, running Git or executing repository configuration. Repository presence is enough to preserve a tree: generic scratch cleanup does not try to decide whether commits are pushed or untracked data is disposable. This occurs only after completion gates and committed state, never by doing destructive work before approval.

The separate `worktree-owner` plugin may then inspect its own verified board/run markers after successful tool completion, check live/shared ownership and archive or remove eligible linked worktrees. CLI/REST preserve Git data but do not run that tool callback. The core safeguard works even when the plugin is disabled.

## Code

- `hermes_cli/kanban_db_workspace.py`: `_scratch_tree_removable`, `_cleanup_workspace`, `_try_cleanup_parent_workspaces`.
- `tests/hermes_cli/test_kanban_scratch_git_preservation.py`: real tool/CLI/REST entry-path regressions and conservative scan tests.

## Tests

Executed with the canonical `scripts/run_tests.sh` and a PM-provisioned test interpreter:

- Before fix: 13 failed, 4 passed in the initial regression set.
- Final focused suite: 34 passed. Real tool success/rejection, real CLI parser/command and mounted REST router success/rejection cover clean, dirty, untracked and unpushed nested repositories; bare and real linked worktrees also covered.
- Broader scratch/database/retention suite: 98 passed, 1 platform skip.
- An additional existing worktree teardown test encountered the real-home Git metadata guard in its bootstrap; not counted as passing or bypassed.
- Bootstrapped public plugin `tests/runtime_fixture.py`: normal plugin discovery and registered callback, actual core tool success/rejection, all four worktree states, real Linux process scan, post-success safe removal. This is candidate-runtime verification, not proof an existing backend reloaded.
- Full public mirror 0001–0269 applied with `git am -3` at merge-base `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`. Existing merge-adapted 0242 retained as documented in `mirror-verification.md`.

## Applying

Apply the numbered series in order, or implement the conservative predicate and both call sites on newer core. A plugin-only post-tool fix cannot save data that an older core already deleted. No hotkeys or configuration defaults change.

## Gotchas

The scan is not atomic against unrelated processes introducing repositories during deletion. It deliberately over-preserves symlink-bearing directories. Preserved scratch needs an explicitly verified Git-aware owner or manual cleanup; no blanket managed-root exemption is warranted. Deploy core safely before enabling lifecycle cleanup; do not restart active sessions to obtain a verification result.

## Why not a plugin alone?

The destructive core `rmtree` runs before `post_tool_call`, including through CLI and REST. The common core boundary must stop treating managed location as proof all descendants are disposable. The plugin owns optional repository-aware teardown; core owns the unconditional data-preservation rule.
