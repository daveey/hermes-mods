# fix(kanban): resume delivery after an exact-head review approval [daveey]

Patch 0738, source `e6324b7636a8c93b5ac60f5fd9ebccc72fc2bdde`.

## Behavior

fix(kanban): resume delivery after an exact-head review approval [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def active_pr_reason(conn: sqlite3.Connection, task_id: str, now: int) -> Option`
- `"""Run-owned dependency review checkpoints for resuming an existing PR.`
- `import re`
- `def apply_block_update(conn, task_id, expected_run_id, kind, new_status, set_sql`
- `def _reviewed_pr(conn, parent_id, parked_at, owner_run_id):`
- `def resumes_review(conn, task_id, pr_urls):`
- `def parked(conn, *, authenticated=True):`
- `def test_completed_reviews_resume_same_pr_despite_later_comments(board):`
- `def test_dependency_and_comments_do_not_grant_generic_retry(board, mode):`
- `To see why a ready card is not spawning, run hermes kanban dispatch --dry-run`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> active_pr held a parked owner after the review parent completed APPROVE
> with a full head SHA, because resumes_review only accepted
> changes_required plus reviewed_sha plus pr. A later comment repeating
> the checkpointed PR is not a new publication. One continuation; the
> next claim still consumes it. An unrelated PR, a new PR, a short head,
> an incomplete review and a crash/reclaim stay held. This is not a
> delivery completion and not a CI bypass.
>
> Problem: t_01a1820bcf616efe stayed ready with no unsatisfied parents and
> respawn_guarded active_pr after review run 3561 (verdict APPROVE, head
> 0d0f9d77a5e5bbb455499b0f4b69bf20201c24c7, no pr).
> Why 1: no accepted handoff event after the PR comment; resumes_review false.
> Why 2: _reviewed_pr requires changes_required + reviewed_sha + pr.
> Why 3: the continuation was repair-only; the production handoff stores head.
> Why 4: tests asserted any approved verdict stays held.
> Root: exact-head approval was not a recognized delivery handoff.
> Not caught: no sequence test for APPROVE+head plus a later same-PR comment.

## Code

- `hermes_cli/kanban_db_dispatch_pr_state.py`
- `hermes_cli/kanban_dependency_review.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_dependency_review.py`

New or changed test definitions present in the source diff:

- `test_exact_head_approval_releases_once_despite_later_same_pr_comment`
- `test_exact_head_approval_of_unrelated_pr_stays_held`
- `test_new_pr_after_exact_head_approval_stays_held`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
