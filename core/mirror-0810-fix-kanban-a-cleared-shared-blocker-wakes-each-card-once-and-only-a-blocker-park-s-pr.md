# fix(kanban): a cleared shared blocker wakes each card once, and only a blocker park's probe clears it [daveey]

Patch 0810, source `334d58b4c32a18f30a7df737ea4a9155a6b87751`.

## Behavior

fix(kanban): a cleared shared blocker wakes each card once, and only a blocker park's probe clears it [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `pipeline down) ends its run with kanban_block(..., blocker_id="<slug>") or`
- `that card probe at once (still one probe at a time). Every card parked behind X`
- `def blocker_states(conn: sqlite3.Connection, *, now: Optional[int] = None) -> di`
- `def release_cleared_blockers(conn: sqlite3.Connection, *, now: Optional[int] = N`
- `def test_park_invariant_kernel_refuses_blocker_park_it_cannot_wake(kanban_home,`
- `_WAKE_CHECK_PROPS = {`
- `_BLOCKER_ID_DESC = (`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> release_cleared_blockers woke every blocked/scheduled card still behind a cleared
> blocker on every tick. A woken card that checked, found the wall still up and
> re-parked without blocker_id (a "pass") stayed behind it, so the next tick woke it
> again: t_01a180e8c9732286 got four Opus runs in ~12 min (2026-10-28 13:57-14:10 PDT).
> Now a card that was already woken for the newest clear (a shared_blocker_cleared
> event at/after cleared_at) or ran since it is skipped; a later clear still wakes it.
>
> A condition_met counted as a clear for any card behind X, whatever probe fired. The
> probe on t_01a1800921b1ff50 was set by a re-park without the id as
> `ac8-live.sh || watch_eve.py`, whose Slack half exited 0 on every new message: six
> false clears of github-askevepodcast-suspended while the API returned 404. Now a
> wake_check exit 0 clears X only when the park that set that check carried X. The
> wake_check / blocker_id schema text says such a check must test only the wall.

## Code

- `hermes_cli/kanban_shared_blockers.py`
- `tools/kanban_tools_schemas.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_blocker_park_wake.py`

New or changed test definitions present in the source diff:

- `test_woken_card_that_reparks_without_id_after_running_is_not_rewoken`
- `test_woken_card_reparked_by_parent_before_running_is_not_rewoken`
- `test_newer_clear_after_a_repark_wakes_the_card_again`
- `test_or_probe_set_by_a_park_without_the_id_does_not_clear`
- `test_wake_check_schema_says_exit_0_must_test_only_the_wall`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
