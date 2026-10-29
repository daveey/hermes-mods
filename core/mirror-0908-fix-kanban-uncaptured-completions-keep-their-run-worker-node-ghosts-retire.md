# fix(kanban): uncaptured completions keep their run; worker-node ghosts retire [daveey]

Patch 0908, source `49745758d7efc93f671c72993a4c52ab345c3773`.

## Behavior

fix(kanban): uncaptured completions keep their run; worker-node ghosts retire [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _owner_attests_terminal(task: dict[str, Any], gw: str, owner_profile: str |`
- `_REPLICA_PROBE = ("SELECT count(*), count(seq), max(seq), total(seq), total(vers`
- `def _read_remote_links(path: Path, now: float | None = None, *,`
- `def _replica_synced(seen_at: dict[str, float], last_ok: float | None) -> tuple:`
- `def _project_replica(path: Path, now: float, me: str, rows: list, info_rows: lis`
- `def _legacy_remote_links() -> list[dict[str, Any]]:`
- `def sync_board(slug: str, path: Path, hub: Hub, gateway: str) -> RoundResult:`
- `def repair_divergence(live: sqlite3.Connection, shadow: sqlite3.Connection, gate`
- `class FakeService:`
- `+"""A remembered remote row whose card a fresh replica proves lives on another gateway is retired. [daveey]`
- `def test_the_dangling_sweep_keeps_the_real_end_of_a_run_the_card_already_finishe`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Two idle-check violations from 10-29, each a class:
>
> 1. hub t_01a185075c59bd52 closed on owner, never released. Its completion landed in a capture-trigger gap.
>    repair_divergence re-queued only the tasks row, so the Board took `done` while the lease's run was still
>    open and ended that run with its placeholder (rows.ts endLeaseRun: released, "card left running (done)").
>    With no later holder write, the placeholder came back over the local done/completed run. The owner
>    observation then published verified=False, and a ledger-bound card is never released without verification.
>    - repair_divergence also queues the card's diverged run rows (same Board version on both sides, nothing in
>      flight), BEFORE the tasks row.
>    - heal_clobbered_completions (primary rounds, after the pull) restores a run that has exactly the
>      placeholder shape: the card is done, the run is its latest, and its latest completed event names the run.
>      The fix goes through capture, so the Board is corrected as well.
>
> 2. MacBook chat 20261022_205332_a60287 kept hub/default/default/t_1f359ff1 open. The hub's worker node had
>    published its copy of a MacBook card as hub-owned (hermes-hub fix: daveey/hermes-hub#59). After eviction the
>    origin's remembered row stayed stale forever, because absence is not closure. A remembered qualified row is
>    retired (never released) only when a fresh replica shows the named owner publishing that exact scope right
>    now without the card, AND another gateway publishing a qualified record with the same board, id and
>    created_at.
>
> Tests (each red on base): test_kanban_sync_run_rows (gap completion, clobber heal, plus a heal guard),
> test_kanban_remote_moved_owner (retire in the same process and at a cold start from the snapshot, plus 3
> unproven-move guards). The fake Board now mirrors rows.ts endLeaseRun.

## Code

- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_sync_client.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/kanban_sync_fake_board.py`
- `tests/hermes_cli/test_kanban_remote_moved_owner.py`
- `tests/hermes_cli/test_kanban_sync_run_rows.py`

New or changed test definitions present in the source diff:

- `test_a_worker_node_copy_gone_from_a_fresh_replica_is_retired`
- `test_an_unproven_move_keeps_the_row_open`
- `test_a_completion_in_a_capture_gap_reaches_the_board_as_completed_not_released`
- `test_a_run_the_boards_placeholder_clobbered_is_restored_from_its_completion`
- `test_the_completion_heal_leaves_a_run_released_for_another_reason`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
