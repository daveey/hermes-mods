# fix(kanban): refuse reclaim of a foreign live lease [daveey]

Patch 0931, source `0d3797b3617f5a442e6d10af407d2eac98c4e7b4`.

## Behavior

fix(kanban): refuse reclaim of a foreign live lease [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def reclaim_task(`
- `from hermes_cli.kanban_db_dispatch_guard_notice import guard_hold_started_at as`
- `def reap_terminal_workers(conn: sqlite3.Connection, *, signal_fn=None) -> list[s`
- `-"""Read-only native dispatcher lane queries."""`
- `def lane_rows(conn: sqlite3.Connection, status: str) -> list[sqlite3.Row]:`
- `def held_by_us_live(conn: sqlite3.Connection, task_id: str, now: Optional[int] =`
- `def test_estop_still_skips_the_whole_tick(tmp_path, monkeypatch):`
- `def test_operator_reclaim_releases_the_lease(primary, lease_client):`
- `def test_fresh_terminal_run_is_left_alone_until_grace_passes(conn):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A replica reclaim of a card another gateway still leases closed the run.
> The close synced, and the owner's terminal-worker reaper SIGTERMed the
> live worker (David-MBP 2026-10-28 23:05, during the core-update drain).
> reclaim_task now records reclaim_refused and leaves the run open. The
> reaper skips a run whose task is still running under that worker. A dead
> worker during a drain hold is requeued to ready and not claimed again
> until the hold lifts.

## Code

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_queries.py`
- `hermes_cli/kanban_db_sync.py`

## Tests

Source test files changed by this commit:

- `tests/agent/test_core_update_drain.py`
- `tests/hermes_cli/test_kanban_sync_leases.py`
- `tests/hermes_cli/test_kanban_terminal_worker_reaper.py`

New or changed test definitions present in the source diff:

- `test_hold_requeues_a_dead_worker_and_does_not_claim_until_the_hold_lifts`
- `test_reclaim_of_a_foreign_live_lease_does_not_end_the_run`
- `test_closed_run_does_not_reap_the_still_running_worker`
- `test_one_failing_row_does_not_abort_the_sweep`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
