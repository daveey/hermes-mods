# Core mirror verification through 0255

## Export

Full ordered non-merge `upstream/main..origin/main` series regenerated through
`0d1cfac0651c714313ff5a0d0953ba0397864996`: 255 patches.
Existing numbered filenames and unchanged patch content were preserved, including
privacy substitutions in 0132, 0174 and 0230. Patch 0242 retains its existing
merge-adapted form: the raw single-commit export conflicts with 0239/0241,
while the committed adaptation applies and preserves the final guard-notice behavior.
No new runtime implementation is introduced by this mirror.

## Executed verification

- All 255 patches applied successfully with `git am -3` at merge-base
  `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63` in an isolated worktree.
- Full patch series private-denylist scan: zero matches.
- Canonical Python runner, serial `-j 1`, on the applied series:
  95 passed, 14 failed, 6 skipped across seven files. All failures were notifier
  tests importing bootstrap after the real-home guard was active. The worktree
  attempted to inspect its live git metadata; a standalone clone rerun instead
  hit the workspace parent's `manifest.json`. Neither failure demonstrated a
  notifier assertion regression, but those 14 cases are not claimed passing.
- Passing files: `test_kanban_model_tier.py` (10),
  `test_kanban_session_activity.py` (18), `test_kanban_wake_check.py` (12),
  `test_kanban_worker_scope_failure.py` (7), `test_web_server_fs.py` (24 passed,
  6 platform skips), `test_live_git_config_guard.py` (10). In addition,
  `test_kanban_notify.py` had 14 passing cases.

Command (paths relative to the applied core tree):

```sh
bash scripts/run_tests.sh -j 1 \
  tests/hermes_cli/test_kanban_notify.py \
  tests/hermes_cli/test_kanban_session_activity.py \
  tests/hermes_cli/test_kanban_wake_check.py \
  tests/hermes_cli/test_kanban_model_tier.py \
  tests/test_live_git_config_guard.py \
  tests/hermes_cli/test_kanban_worker_scope_failure.py \
  tests/hermes_cli/test_web_server_fs.py
```

Desktop TypeScript tests, actual UI rendering and packaged Electron were not
rerun for this export. See individual feature docs for shipped regression files.
