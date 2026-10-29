# test(kanban): pin the replica clock in exact stale_age assertions [daveey]

Patch 0960, source `8d288f1b685e4188a1ef08d5de334a7f68e5eb3c`.

## Behavior

test(kanban): pin the replica clock in exact stale_age assertions [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_fallback_status_including_terminal_records(replica, status, rc):`
- `def test_schedule_awaiting_card_on_stale_replica_is_accepted(replica, board, mon`
- `def test_block_awaiting_card_on_stale_replica_is_accepted(replica, board, monkey`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The reader reports live age, rounded to a second. A deschedule between
> make_stale and the re-read printed stale_age=1239s for an age the test
> set to 1237 (CI run 40860781974). Pin time.time before those assertions,
> and check the stored observation rather than a payload dump that can
> echo an earlier read.

## Code

- `tests/hermes_cli/test_kanban_wait_replica.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_wait_replica.py`

New or changed test definitions present in the source diff:

- `test_deschedule_changes_rounded_stale_age_unless_clock_is_pinned`
- `test_missing_is_error_and_stale_known_card_is_unknown_not_yet`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
