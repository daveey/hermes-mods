# fix(kanban): a park with blocker_id keeps its wake, and a blocker with only blocked cards still gets a probe [daveey]

Patch 0743, source `c52339ad1359cda816f7fd72bc739f10751c03ee`.

## Behavior

fix(kanban): a park with blocker_id keeps its wake, and a blocker with only blocked cards still gets a probe [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _guarded_park(conn, op: str, wake, refused: dict, park, awaiting=None, **pla`
- `def edit_task(`
- `def block_task(`
- `class DispatchResult:`
- `def _run_reclaim_phase(`
- `def _cmd_dispatch(args: argparse.Namespace) -> int:`
- `from typing import Optional`
- `def release_cleared_blockers(conn: sqlite3.Connection, *, now: Optional[int] = N`
- `def render_blocker_context(conn: sqlite3.Connection, task_id: str) -> str:`
- `def implied_wake(payload: dict, event_at: int) -> int:`
- `def park(`
- `def planned_wake(conn: sqlite3.Connection, task_id: str, *, op: str, reason: Opt`
- `def test_cli_block_agent_owned_allowed_with_wake_check(kanban_home):`
- `def test_block_tool_validates_and_reports_blocker(kanban_home, monkeypatch):`
- `def test_block_agent_owned_is_rejected_with_typed_error(worker_env, kind):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> block_task skipped the timed park whenever blocker_id was set, so kind=time /
> SCHEDULED_UNTIL parks behind a shared blocker landed 'blocked' with no wake_at
> and their wake_check was silently dropped (t_e7dc822c). And since only ready
> cards were spaced into probes, a blocker whose cards were all 'blocked' was
> never probed or cleared.
>
> - timed parks (kind=time or SCHEDULED_UNTIL) behind a blocker park scheduled
>   with their wake and wake check; blocker_id rides on the closed run so the
>   card still counts as behind the blocker
> - probe_stranded_blockers: when no card behind an open blocker is ready or
>   running and the probe interval has passed, unblock one blocked card (no
>   human ask) as the probe
> - an agent-owned block (no kind/capability/transient/needs_input, no
>   human_request) that brings a wake_check / clock_only parks scheduled on it
>   (subject to the usual producer gate) instead of landing blocked and dropping
>   the probe; on any other block the probe is refused with the reason
> - worker context says a timed wait keeps its wake with blocker_id

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_ops.py`
- `hermes_cli/kanban_shared_blockers.py`
- `hermes_cli/kanban_timed_park.py`
- `hermes_cli/kanban_wake_check.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_cli.py`
- `tests/hermes_cli/test_kanban_shared_blockers.py`
- `tests/tools/test_kanban_tools.py`

New or changed test definitions present in the source diff:

- `test_timed_park_with_blocker_keeps_wake_and_probe`
- `test_planned_wake_counts_blocker_parks`
- `test_wake_check_on_agent_owned_block_is_honored_not_dropped`
- `test_wake_check_on_human_ask_block_is_refused_not_dropped`
- `test_lone_blocked_card_behind_open_blocker_is_probed_after_interval`
- `test_all_blocked_behind_blocker_get_exactly_one_probe`
- `test_human_ask_behind_blocker_is_not_auto_probed`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
