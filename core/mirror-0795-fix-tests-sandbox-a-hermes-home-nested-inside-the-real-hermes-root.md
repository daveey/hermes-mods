# fix(tests): sandbox a HERMES_HOME nested inside the real Hermes root [daveey]

Patch 0795, source `89338dbf512ba2673de00a98ba13c3c3e60c8f3b`.

## Behavior

fix(tests): sandbox a HERMES_HOME nested inside the real Hermes root [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _hermes_home_points_at_production(value: str) -> bool:`
- `def test_hermes_exported_scratch_tmp_is_not_the_test_temp_root(tmp_path):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> HERMES_HOME=$(mktemp -d) from a Hermes worker shell (TMPDIR=~/.hermes/cache/scratch)
> handed pytest a "custom" home inside the real root. The conftest honored it, so
> import-time homes (tui_gateway.server._hermes_home) froze there and realpath() of
> them lstat'ed ~/.hermes/cache, tripping the real-home guard in 13 notification_poller
> tests. Treat any home nested in the real root as production and sandbox it.
> Regression: test_hermes_home_nested_in_the_real_root_is_sandboxed.

## Code

- `tests/conftest.py`
- `tests/hermes_cli/test_real_home_tripwire.py`

## Tests

Source test files changed by this commit:

- `tests/conftest.py`
- `tests/hermes_cli/test_real_home_tripwire.py`

New or changed test definitions present in the source diff:

- `test_hermes_home_nested_in_the_real_root_is_sandboxed`
- `test_session_home_is_outside_the_real_root`
- `test_import_time_home_resolves_under_the_guard`
- `test_linked_worktree_git_dir_is_the_checkouts_own_metadata`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
