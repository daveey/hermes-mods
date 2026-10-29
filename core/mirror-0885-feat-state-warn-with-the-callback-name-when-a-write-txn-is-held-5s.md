# feat(state): warn with the callback name when a write txn is held >=5s [daveey]

Patch 0885, source `3f166adee6d66385d8598a58ac059cd7c89e5612`.

## Behavior

feat(state): warn with the callback name when a write txn is held >=5s [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _foreign_state_db_holders(db_path: Path) -> list[tuple[int, str]]:`
- `class SessionDB(`
- `def test_duplicate_notice_while_another_process_holds_write_lock(db, tmp_path):`

## Code

- `hermes_state.py`

## Tests

Source test files changed by this commit:

- `tests/test_kanban_notice_write_lock_hold.py`

New or changed test definitions present in the source diff:

- `test_slow_write_txn_logs_its_callback_name`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
