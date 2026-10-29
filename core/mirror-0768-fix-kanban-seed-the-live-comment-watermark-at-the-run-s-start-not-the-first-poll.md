# fix(kanban): seed the live comment watermark at the run's start, not the first poll [daveey]

Patch 0768, source `91359bea619716f9a5dd98cff949741d13bc487e`.

## Behavior

fix(kanban): seed the live comment watermark at the run's start, not the first poll [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def delivered_comment_ids(conn: sqlite3.Connection, task_id: str, run_id: int) -`
- `def test_kanban_show_lists_undelivered_live_run_comments(worker_home, monkeypatc`
- `def heartbeat_current_worker_from_env() -> bool:`
- `def inject_new_comments_from_env(agent: Any) -> bool:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A comment posted after the worker's prompt was built but before its first
> activity tick was swallowed by the first-poll seed and never steered in.
> The first poll now seeds at the max comment id created before
> task_runs.started_at for this run, then delivers newer comments through the
> normal filter (own ids, empty bodies, ids already in comment_delivered).
> Falls back to the old seed-at-now when the run can't be resolved.

## Code

- `hermes_cli/kanban_db.py`
- `tools/kanban_tools.py`

## Tests

Source test files changed by this commit:

- `tests/tools/test_kanban_comment_injection.py`

New or changed test definitions present in the source diff:

- `test_comment_after_run_start_before_first_poll_is_injected`
- `test_comment_before_run_start_is_not_injected_on_first_poll`
- `test_own_comment_before_first_poll_is_not_injected`
- `test_first_poll_skips_comments_already_delivered_to_this_run`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
