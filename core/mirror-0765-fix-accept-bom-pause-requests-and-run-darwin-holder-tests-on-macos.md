# fix: accept BOM pause requests and run Darwin holder tests on macOS [daveey]

Patch 0765, source `35ed449284a6db7aab3988c104fe486dfad1c8bc`.

## Behavior

fix: accept BOM pause requests and run Darwin holder tests on macOS [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def pause_requested(home):`
- `def test_request_under_the_hold_is_active(tmp_path):`
- `def test_windows_restart_manager_scan_sizes_then_excludes_self(monkeypatch, tmp_`
- `def darwin_scan(tmp_path, monkeypatch):`
- `def test_darwin_scan_never_touches_foreign_files_and_finds_holder_by_identity(da`
- `def test_darwin_scan_quiet_store_is_clean_despite_foreign_files(darwin_scan):`
- `def test_darwin_scan_counts_a_retired_generation_and_case_folded_path(darwin_sca`

## Code

- `hermes_cli/admission_fence.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_admission_fence_pause.py`
- `tests/hermes_state/test_state_db_holders.py`

New or changed test definitions present in the source diff:

- `test_bom_prefixed_request_under_hold_is_active`
- `test_request_without_the_hold_is_ignored`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
