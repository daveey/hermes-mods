# Preserve Git-bearing Kanban scratch workspaces

Patch: 0294. Fork commit: `20358a17bdec17ed5692d9be0f4b99b60c3c4ddf`.

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
- Full public mirror 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126, 0127, 0128, 0129, 0130, 0131, 0132, 0133, 0134, 0135, 0136, 0137, 0138, 0139, 0140, 0141, 0142, 0143, 0144, 0145, 0146, 0147, 0148, 0149, 0150, 0151, 0152, 0153, 0154, 0155, 0156, 0157, 0158, 0159, 0160, 0161, 0162, 0163, 0164, 0165, 0166, 0167, 0168, 0169, 0170, 0171, 0172, 0173, 0174, 0175, 0176, 0177, 0178, 0179, 0180, 0181, 0182, 0183, 0184, 0185, 0186, 0187, 0188, 0189, 0190, 0191, 0192, 0193, 0194, 0195, 0196, 0197, 0198, 0199, 0200, 0201, 0202, 0203, 0204, 0205, 0206, 0207, 0208, 0209, 0210, 0211, 0212, 0213, 0214, 0215, 0216, 0217, 0218, 0219, 0220, 0221, 0222, 0223, 0224, 0225, 0226, 0227, 0228, 0229, 0230, 0231, 0232, 0233, 0234, 0235, 0236, 0237, 0238, 0239, 0240, 0241, 0242, 0243, 0244, 0245, 0246, 0247, 0248, 0249, 0250, 0251, 0252, 0253, 0254, 0255, 0256, 0257, 0258, 0259, 0260, 0261, 0262, 0263, 0264, 0265, 0266, 0267, 0268, 0269, 0270, 0271, 0272, 0273, 0274, 0275, 0276, 0277, 0278, 0279, 0280, 0281, 0282, 0283, 0284, 0285, 0286, 0287, 0288, 0289, 0290, 0291, 0292, 0293, 0294 applied with `git am -3 --empty=keep` at merge-base `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`. Existing merge-adapted 0251 retained as documented in `mirror-verification.md`.

## Applying

Apply the numbered series in order, or implement the conservative predicate and both call sites on newer core. A plugin-only post-tool fix cannot save data that an older core already deleted. No hotkeys or configuration defaults change.

## Gotchas

The scan is not atomic against unrelated processes introducing repositories during deletion. It deliberately over-preserves symlink-bearing directories. Preserved scratch needs an explicitly verified Git-aware owner or manual cleanup; no blanket managed-root exemption is warranted. Deploy core safely before enabling lifecycle cleanup; do not restart active sessions to obtain a verification result.

## Why not a plugin alone?

The destructive core `rmtree` runs before `post_tool_call`, including through CLI and REST. The common core boundary must stop treating managed location as proof all descendants are disposable. The plugin owns optional repository-aware teardown; core owns the unconditional data-preservation rule.
