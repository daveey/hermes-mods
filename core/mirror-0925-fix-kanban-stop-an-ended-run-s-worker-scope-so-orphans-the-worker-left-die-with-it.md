# fix(kanban): stop an ended run's worker scope so orphans the worker left die with it [daveey]

Patch 0925, source `d5b8545b4de182ee198997da5cdb7e1d9245dfc7`.

## Behavior

fix(kanban): stop an ended run's worker scope so orphans the worker left die with it [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def notify_task_updated(`
- `class DispatchResult:`
- `def _run_reclaim_phase(`
- `def _cmd_dispatch(args: argparse.Namespace) -> int:`
- `def release_failure(metadata: dict) -> None:`
- `+"""Invariant: a run's hermes-worker-kanban-<task>-run-<n>.scope is stopped once`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A worker's hermes-worker-kanban-<task>-run-<n>.scope outlived the worker:
> nothing stopped it, so whatever the agent left behind (a background pytest
> whose wrapper it pkill'ed, ssh-agent -s, wait loops) kept running for hours
> or days (hub test slots held 6h45m; 33 scopes, some 6 days old).
>
> Each dispatch tick (throttled to 5 min per board) now stops the scope of
> every run of this board that ended >10 min ago and whose retained worker is
> gone (systemctl --user stop --no-block: SIGTERM, SIGKILL after
> TimeoutStopSec). Jobs started with persist_on_release (any profile's
> processes.json, pid start time checked) and their descendants are spared:
> such a scope gets only its other processes signalled. Every stop is logged
> and booked as a run_scope_stopped event listing the killed pids/commands.

## Code

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_ops.py`
- `hermes_cli/kanban_worker_scope.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_run_scope_stop.py`

New or changed test definitions present in the source diff:

- `test_ended_runs_scope_is_stopped_on_dispatch_tick`
- `test_persisted_job_survives_and_only_the_orphan_is_killed`
- `test_same_pid_without_persisted_marker_is_not_spared`
- `test_scope_is_left_alone_inside_grace_while_running_or_on_another_board`
- `test_scope_with_live_retained_worker_waits_for_the_worker_reaper`
- `test_sweep_is_throttled_and_never_raises`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
