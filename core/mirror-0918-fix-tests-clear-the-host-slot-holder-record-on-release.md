# fix(tests): clear the host slot holder record on release [daveey]

Patch 0918, source `fb855a70456e4354840e732728326c7e4d1c1432`.

## Behavior

fix(tests): clear the host slot holder record on release [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def acquire_host_slot(on_wait=None) -> int | None:`
- `def _release_host_test_slot(config) -> None:`
- `def test_live_young_holder_is_not_reclaimed(slot_env, monkeypatch, tmp_path):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A record left by a released slot names a dead pid; an older checkout's runner that takes the slot next
> writes none, so a waiter would read the stale record and reclaim a live holder's slot.

## Code

- `scripts/run_tests_host_slots.py`

## Tests

Source test files changed by this commit:

- `tests/conftest.py`
- `tests/scripts/test_run_tests_host_slots.py`

New or changed test definitions present in the source diff:

- `test_release_clears_the_holder_record`
- `test_slot_max_age_env_and_config`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
