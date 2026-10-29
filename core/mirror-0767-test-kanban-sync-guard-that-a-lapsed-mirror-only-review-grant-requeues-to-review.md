# test(kanban-sync): guard that a lapsed mirror-only review grant requeues to review [daveey]

Patch 0767, source `c074a30228f7add59febf1f60066ae7cf08f642c`.

## Behavior

test(kanban-sync): guard that a lapsed mirror-only review grant requeues to review [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `Implements the contract the client relies on: row ops (per-field LWW by HLC on t`
- `class FakeHub:`
- `def test_board_grant_row_mid_claim_is_not_reconciled(live, kanban_home, svc):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Follow-up to 9f23cabf55 (t_01a182bbad9df5ac). Hypothesis: a review card written
> running by the Board's lease grant, whose local claim never landed, would be
> requeued to ready by reconcile_orphaned_running once the 180 s grace and the
> lease lapsed, because _retry_status_for_run reads the current run's claimed event.
>
> It does not happen against the real Board: grantLease (hermes-hub
> worker/src/transitions.ts) writes a claimed event with run_uid and
> source_status=review in the grant transaction; it syncs down bound to the grant's
> run, which current_run_uid points at. The fake Board omitted that event (the new
> test was RED against it for both a fresh review card and one after an
> implementation run); it now emits it like the Board does, and the test is a guard.

## Code

- `tests/hermes_cli/kanban_sync_fake_board.py`
- `tests/hermes_cli/test_kanban_sync_run_rows.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/kanban_sync_fake_board.py`
- `tests/hermes_cli/test_kanban_sync_run_rows.py`

New or changed test definitions present in the source diff:

- `test_board_grant_row_for_review_card_requeues_to_review_after_grace`
- `test_a_run_that_ends_before_its_first_push_survives_the_grants_own_run_row`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
