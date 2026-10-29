# fix(state): revalidate macOS deleted-WAL holders after path comparison [daveey]

Patch 0800, source `566cbacc5333bc5101a7e9ca66e5f27c6be7eedc`.

## Behavior

fix(state): revalidate macOS deleted-WAL holders after path comparison [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _darwin_all_pids(lib) -> list[int]:`
- `def _iter_darwin_fd_targets():`
- `class _DarwinFdScan:`
- `def _iter_darwin_sidecar_holders(db_path) -> list[tuple[int, str]]:`
- `+"""A scan snapshot is not evidence that a descriptor is still held at verdict time."""`
- `def _scan_threads():`
- `def test_darwin_concurrent_callers_during_slow_scan_share_one_scan_thread(tmp_pa`

## Code

- `hermes_state_dbfile.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_state/test_darwin_holder_snapshot.py`
- `tests/hermes_state/test_deleted_wal_generation_guard.py`

New or changed test definitions present in the source diff:

- `test_scan_revalidates_descriptors_before_publishing`
- `test_revalidation_is_bounded_and_does_not_cache_timeout`
- `test_native_scan_does_not_refuse_after_normal_sidecar_close`
- `test_descriptor_closing_during_path_comparison_is_not_a_holder`
- `test_late_watcher_joins_while_verdict_is_computed_without_lock`
- `test_darwin_concurrent_callers_during_slow_scan_share_one_scan_thread`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
