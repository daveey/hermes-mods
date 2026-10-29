# state: preflight ignores state.db quarantined mid-check (deflake concurrent quarantine test)

Patch 0833, source `3a0ba542f30b301e5c8b9d40cbad36f4ff613d78`.

## Behavior

state: preflight ignores state.db quarantined mid-check (deflake concurrent quarantine test). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def preflight_db_writability(db_path: Path, *, db_label: str = "state.db") -> No`
- `def test_live_connection_0_byte_not_quarantined_in_process(tmp_path, monkeypatch`

## Code

- `hermes_state_repair.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_state/test_zeroed_state_db.py`

New or changed test definitions present in the source diff:

- `test_preflight_ignores_file_quarantined_mid_check`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
