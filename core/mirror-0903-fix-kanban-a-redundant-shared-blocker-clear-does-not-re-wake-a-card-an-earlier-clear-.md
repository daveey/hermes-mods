# fix(kanban): a redundant shared-blocker clear does not re-wake a card an earlier clear released [daveey]

Patch 0903, source `fe4027a3bde1ef1a5ccc43527d86ba13919d5c37`.

## Behavior

fix(kanban): a redundant shared-blocker clear does not re-wake a card an earlier clear released [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `class BlockerState:`
- `def _probe_parked_behind(conn: sqlite3.Connection, kb, task_id: str, met, blocke`
- `def _already_saw_clear(conn: sqlite3.Connection, kb, task_id: str, st: BlockerSt`
- `def _age_all(conn, seconds: int) -> None:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> release_cleared_blockers measured "this card already saw the clear" against the NEWEST clear
> (cleared_at). A second card ending a run with clear_blocker=true, with no re-assertion in
> between, moved cleared_at forward and woke every card the first clear had already released.
> Now BlockerState.first_cleared_at records the earliest clear after the newest assertion, and
> _already_saw_clear compares against it. A later clear wakes a card again only after X was
> re-asserted. cleared_at/cleared_by/cleared_via (reporting) and is_open are unchanged.
>
> Observed 2026-10-28 (MacBook board): t_e7dc822c behind github-askevepodcast-suspended was woken
> at 15:41:14 PDT (cleared by t_01a1800921b1ff50 run 9464, correct) and again at 15:53:38 PDT
> (cleared by t_01a180e8c9732286 run 9472, spurious; it had re-parked at 15:47:28 without the id).
>
> Five whys:
> - Why 1: _already_saw_clear(t_e7dc822c) returned False: it compared against st.cleared_at, the
>   newest clear (15:53:28); the card's shared_blocker_cleared event (15:41:14) and latest run
>   start (15:41:37) were both older.
> - Why 2: cleared_at moved forward because a second card behind X ended a run with
>   clear_blocker=true; blocker_states calls _mark_cleared for every behind card's clearing run,
>   even when X is already cleared with no assertion since.
> - Why 3: t_e7dc822c stays in `behind` after its release: re-parks without the id are "pass"
>   runs (448e876df2 semantics), so it remains behind X indefinitely.
> - Root (happened): "already saw the clear" was measured against the newest clear, not the first
>   clear since the newest assertion, so a redundant clear counted as a new event.
> - Root (not caught): the 334d58b4c3 tests covered a single clearing card and per-tick re-wakes;
>   none had two cards clear the same blocker in sequence while a third sat behind it.
> - Recurrence: third defect in this module in a day (448e876df2, 334d58b4c3); agents cannot know
>   whether another card already cleared X, so a clear must be idempotent.
>
> Tests: test_redundant_clear_by_another_card_does_not_rewake (A, B, C behind X; A clears, C woken
> once; C re-parks; B clears; release returns [], no new event/comment) and
> test_clear_after_a_reassertion_still_wakes_after_a_redundant_clear (D re-asserts X, then clears;
> C woken again). Both fail on 2f26519116 and pass with the fix.

## Code

- `hermes_cli/kanban_shared_blockers.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_blocker_park_wake.py`

New or changed test definitions present in the source diff:

- `test_redundant_clear_by_another_card_does_not_rewake`
- `test_clear_after_a_reassertion_still_wakes_after_a_redundant_clear`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
