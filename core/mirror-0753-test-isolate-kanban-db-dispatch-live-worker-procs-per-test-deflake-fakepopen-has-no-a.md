# test: isolate kanban_db_dispatch._live_worker_procs per test (deflake '_FakePopen' has no attribute 'poll')

Patch 0753, source `998234cb3e68544f5a7c5869b8592208ed3f420e`.

## Behavior

test: isolate kanban_db_dispatch._live_worker_procs per test (deflake '_FakePopen' has no attribute 'poll'). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _reset_foreground_exit_fence():`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> _default_spawn parks Popen handles in a module-global registry that
> reap_worker_zombies() polls. Tests that patch subprocess.Popen with a
> minimal fake leaked it there, so a later dispatch test in the same
> process crashed in reap_worker_zombies (CI run 40676146435, flaky).
> An autouse root-conftest fixture now empties the registry before each
> test and restores it after, covering every current and future fake.
>
> Repro: test_dispatcher_spawn_injects_kanban_paths_without_stale_session
> then test_dispatch_max_in_progress_blocks_review_when_at_limit: RED before, GREEN after.

## Code

- `tests/conftest.py`

## Tests

Source test files changed by this commit:

- `tests/conftest.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
