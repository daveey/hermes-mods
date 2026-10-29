# fix(state): kanban notice dedupe no longer scans messages or takes the write lock when already delivered [daveey]

Patch 0882, source `593b19f369d23a93b1024337838a638a6e33da3b`.

## Behavior

fix(state): kanban notice dedupe no longer scans messages or takes the write lock when already delivered [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _stale_holder(row, now: float) -> bool:`
- `class SessionMessagesMixin:`
- `+"""A kanban notice that is already delivered must not hold the state.db write lock. [daveey]`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> py-spy of a hermes-hub serve holding the state.db write lock caught the unloaded-notice sweep in
> record_kanban_notice's BEGIN IMMEDIATE txn running a notice_id query planned as SCAN over all messages.
> Drive it from the lineage sessions (index search) and answer already-delivered notices from a read connection.

## Code

- `hermes_state_messages.py`

## Tests

Source test files changed by this commit:

- `tests/test_kanban_notice_write_lock_hold.py`

New or changed test definitions present in the source diff:

- `test_notice_dedupe_query_never_scans_messages`
- `test_already_delivered_notice_takes_no_write_lock`
- `test_duplicate_notice_while_another_process_holds_write_lock`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
