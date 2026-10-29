# [daveey] Extend orphan install-state GC with idle-safe hourly host sweeps

Patch 0947, source `cee338f7fcd744611b234823b046fcc91af9ccd5`.

## Behavior

[daveey] Extend orphan install-state GC with idle-safe hourly host sweeps. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `-"""Reclaim per-checkout dependency state whose checkout is gone.`
- `def _provably_deleted(root: Path, data_root: Path) -> bool:`
- `from __future__ import annotations`
- `def _state(installs, key, project_root):`
- `def test_held_orphan_is_kept(tmp_path):`
- `def test_partly_removed_orphan_keeps_its_record_until_retried(tmp_path):`
- `+"""Host-local GC extends the existing collector, retaining age and lock guards."""`
- `hermes pm install chromium`

## Code

- `pm/install_states.py`
- `website/docs/reference/package-management.md`

## Tests

Source test files changed by this commit:

- `tests/pm/test_install_states_gc.py`
- `tests/pm/test_install_states_hourly_gc.py`

New or changed test definitions present in the source diff:

- `test_startup_prune_reclaims_only_provably_deleted_checkouts`
- `test_hourly_collection_contract`
- `test_nested_recent_touch_is_kept`
- `test_generation_lease_keeps_idle_orphan`
- `test_cli_dry_run_then_sweep_then_empty`
- `test_install_lock_remains_held_during_deletion`
- `test_dry_run_preserves_unlocked_lease_and_absent_lock`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
