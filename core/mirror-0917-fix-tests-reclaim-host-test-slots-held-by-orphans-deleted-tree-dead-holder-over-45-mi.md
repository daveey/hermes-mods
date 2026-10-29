# fix(tests): reclaim host test slots held by orphans (deleted tree, dead holder, over 45 min) [daveey]

Patch 0917, source `5109888869913294bdf60578c1a5faa4b5f0f673`.

## Behavior

fix(tests): reclaim host test slots held by orphans (deleted tree, dead holder, over 45 min) [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `The cap is HERMES_TEST_HOST_SLOTS, else the host's tests.host_slots in `
- `def host_slots_enabled() -> bool:`
- `def _try_slot(path: str) -> int | None:`
- `def acquire_host_slot(on_wait=None) -> int | None:`
- `from __future__ import annotations`
- `def slot_env(tmp_path, monkeypatch):`
- `def test_direct_pytest_n8_is_capped_to_the_slot_budget(tmp_path):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Two direct pytest runs whose worktrees had been removed hung for 6h45m on hermes-hub holding 2 of the
> 4 host test slots; every scripts/run_tests.sh on the host queued behind them. A slot now records its
> holder (pid, Linux start time, acquire time, cwd). A waiter that finds every slot taken reclaims (unlinks) a
> lock whose holder runs in a deleted tree (SIGKILLed: the hung pytest ignored SIGTERM), whose recorded
> pid is gone or reused, or that has been held past HERMES_TEST_SLOT_MAX_AGE / tests.slot_max_age_s
> (default 45 min; that holder is left running).

## Code

- `scripts/run_tests_host_slots.py`

## Tests

Source test files changed by this commit:

- `tests/scripts/test_run_tests_host_slots.py`

New or changed test definitions present in the source diff:

- `test_slot_held_from_a_deleted_tree_is_reclaimed`
- `test_slot_held_past_the_age_cap_is_reclaimed_without_killing`
- `test_slot_kept_by_an_inherited_fd_of_a_dead_holder_is_reclaimed`
- `test_live_young_holder_is_not_reclaimed`
- `test_slot_max_age_env_and_config`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
