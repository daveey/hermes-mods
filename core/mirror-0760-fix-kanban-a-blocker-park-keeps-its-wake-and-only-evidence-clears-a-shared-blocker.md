# fix(kanban): a blocker park keeps its wake, and only evidence clears a shared blocker [daveey]

Patch 0760, source `448e876df2e16ce4f26ac5b6a27b1ae93b07defd`.

## Behavior

fix(kanban): a blocker park keeps its wake, and only evidence clears a shared blocker [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `import sqlite3`
- `def _refuse_untimed_probe(kind: Optional[str], wake_check: Optional[str], clock_`
- `def block_task(`
- `def schedule_task(`
- `pipeline down) ends its run with kanban_block(..., blocker_id="<slug>") or`
- `NEUTRAL_OUTCOMES = frozenset({`
- `class BlockerState:`
- `def blocker_states(conn: sqlite3.Connection, *, now: Optional[int] = None) -> di`
- `def _unblocked_since_assertion(conn: sqlite3.Connection, task_id: str, blocker_i`
- `def shared_blocker_guard(`
- `def render_blocker_context(conn: sqlite3.Connection, task_id: str) -> str:`
- `def implied_wake(payload: dict, event_at: int) -> int:`
- `def park(`
- `def validate_park(*, wake_at: Optional[int], wake_check: Optional[str],`
- `def planned_wake(conn: sqlite3.Connection, task_id: str, *, op: str, reason: Opt`
- `def test_repeated_agent_block_stays_agent_but_approval_stays_human(board):`
- `+"""Shared-blocker parks keep their way back, and only evidence clears the blocker. [daveey]`
- `def _blocked_behind(conn, title: str, blocker_id=BID) -> str:`
- `def test_block_records_blocker_on_run_and_event(kanban_home):`
- `def test_unblocked_cards_behind_open_blocker_get_one_probe(kanban_home, spawns):`
- `def test_cleared_blocker_releases_blocked_cards_without_human_unblock(kanban_hom`
- `def test_wake_check_on_human_ask_block_is_refused_not_dropped(kanban_home):`
- `def test_lone_blocked_card_behind_open_blocker_is_probed_after_interval(kanban_h`
- `def test_human_ask_behind_blocker_is_not_auto_probed(kanban_home, spawns):`
- `def test_needs_input_without_marker_still_blocks_for_a_human(kanban_home: Path)`
- `def _blocker_id_arg(args: dict) -> Optional[str]:`
- `def _park_guard(conn, tid: str, op: str, wake: tuple, **plan: Any) -> None:`
- `def _handle_block(args: dict, **kw) -> str:`
- `def _handle_schedule(args: dict, **kw) -> str:`
- `def _handle_continue(args: dict, **kw) -> str:`
- `HUMAN_REQUEST = {`
- `KANBAN_BLOCK_SCHEMA = _schema(`
- `KANBAN_SCHEDULE_SCHEMA = _schema(`
- `KANBAN_CONTINUE_SCHEMA = _schema(`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> 2026-10-28: two cards called kanban_block with SCHEDULED_UNTIL, a wake_check and
> blocker_id=github-askevepodcast-suspended. block_task skipped the timed park
> whenever blocker_id was set, so both landed 'blocked' with no wake_at and no
> task_wake_checks row. A parent chat then re-parked one with kanban_schedule and
> no id; blocker_states counted that run as a clear, and release_cleared_blockers
> unblocked the other card while the account was still suspended.
>
> - kanban_timed_park.park_mode decides how block_task parks. A blocker never
>   cancels a timed park (SCHEDULED_UNTIL / kind=time / wake_check / clock_only):
>   the card parks 'scheduled' with its wake and probe, and blocker_id rides on
>   the closed run and the event, so the card stays behind the blocker.
> - A blocker park with no wake, no human ask and no open parent is given one on
>   the shared probe cadence (blocker_wake_seconds), so a blocker whose cards are
>   all parked still gets probed. A human ask behind a blocker stays a block.
> - run_effect: a run asserts X (carries the id), clears it (completed /
>   review handoff, or clear_blocker=true), or passes (neutral outcomes and any
>   re-park without the id). A condition_met (wake_check exit 0) on a card behind
>   X and a run naming a different blocker also clear it.
> - release_cleared_blockers wakes 'scheduled' cards behind a cleared blocker as
>   well as 'blocked' ones.
> - kanban_block / kanban_schedule / kanban_continue take clear_blocker; the
>   schemas and the probe context describe the blocker_id semantics.
>
> Tests: tests/hermes_cli/test_kanban_blocker_park_wake.py reproduces the 04:15
> block and the 04:28 re-park for schedule/block/continue by worker and parent,
> and checks every block park keeps a way back; it fails on 76ff5aa9fa.

## Code

- `hermes_cli/kanban_block_routing.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_schedule.py`
- `hermes_cli/kanban_shared_blockers.py`
- `hermes_cli/kanban_timed_park.py`
- `hermes_cli/kanban_wake_check.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_agent_stop_routing.py`
- `tests/hermes_cli/test_kanban_blocker_park_wake.py`
- `tests/hermes_cli/test_kanban_shared_blockers.py`
- `tests/hermes_cli/test_kanban_timed_park.py`

New or changed test definitions present in the source diff:

- `test_block_with_blocker_and_scheduled_until_parks_with_wake`
- `test_block_with_blocker_and_no_wake_is_given_one`
- `test_blocker_park_with_human_request_stays_a_visible_block`
- `test_repark_without_blocker_id_keeps_blocker_open`
- `test_completion_clears_and_wakes_parked_cards`
- `test_explicit_clear_blocker_opt_out`
- `test_wake_check_success_on_a_card_behind_clears`
- `test_every_block_park_has_a_way_back_or_is_refused`
- `test_park_invariant_kernel_refuses_blocker_park_it_cannot_wake`
- `test_blocker_wake_and_stranded_probe_together_yield_one_probe`
- `test_shared_blocker_dependency_parks_with_a_wake`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
