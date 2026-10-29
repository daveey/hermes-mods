# fix(kanban): orphan sweep restores a review run to review and skips claims in flight [daveey]

Patch 0742, source `9f23cabf55131bbc23db2bb56ae3a4e4454aa251`.

## Behavior

fix(kanban): orphan sweep restores a review run to review and skips claims in flight [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def detect_stale_running(`
- `def reconcile_orphaned_running(conn: sqlite3.Connection) -> list[str]:`
- `def release_lease(conn: sqlite3.Connection, task_id: str, run_uid: Optional[str]`
- `Inspired by openai/symphony's tracker reconciliation (Apache-2.0), idea-level.`
- `def _orphan_running(conn, tid, *, claim_lock=None, claim_expires=None,`
- `class TestReconcileOrphanedRunning:`
- `def test_stale_lock_reclaim_survives_the_echo_of_the_runs_earlier_push(live, kan`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> reconcile_orphaned_running hard-coded status='ready'. Every other release path uses
> _retry_status_for_run, so a reviewer run caught by the sweep became an implementation run
> (MacBook t_01a18189ff87b1c1, 2026-10-28: an Opus worker redid finished work).
>
> The sweep also fired on claims still being published. On a primary board the Board's lease
> grant writes the card 'running'; the feed applies that row (_upsert_task + _mirror_lease)
> before, or instead of, the local claim CAS (claim_and_open_run sets claim_lock atomically, so
> it is not the source). The card reads running/claim_lock NULL with our lease mirrored, and the
> sweep requeued it (39 false "dead/gone worker" reconciles on 8 cards in one drain window).
>
> Now: requeue to _retry_status_for_run (payload retry_status, comment names it); defer a card
> with a 'claimed' event younger than ORPHAN_CLAIM_GRACE_SECONDS (180 s = grant RPC 30 s + claim
> txn busy timeout 120 s + release RPC 30 s) or a live Board lease this gateway holds.

## Code

- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_sync.py`

## Tests

Source test files changed by this commit:

- `tests/gateway/test_kanban_reconcile_orphans.py`
- `tests/hermes_cli/test_kanban_sync_run_rows.py`

New or changed test definitions present in the source diff:

- `test_review_run_orphan_requeued_to_review`
- `test_implementation_run_orphan_still_requeued_to_ready`
- `test_card_claimed_within_grace_is_not_reconciled`
- `test_non_running_statuses_ignored`
- `test_board_grant_row_mid_claim_is_not_reconciled`
- `test_a_run_that_ends_before_its_first_push_survives_the_grants_own_run_row`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
