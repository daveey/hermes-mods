# Cross-board ranking of shared hub worker slots

Patches: **2502–2503**.

## Behavior

`kanban.max_in_progress` caps the whole host, but each board's dispatcher tick used to take every free slot for its own ready cards, in a rotating board order. A freed slot therefore went to whichever board ticked first, not to the best card, and a high-priority card on one board could wait while lower-priority cards on another board started. Now, when free slots are fewer than the dispatchable cards across all boards, every board's ready (and, when review dispatch is on, review) candidates are ranked together and each board gets a spawn quota equal to how many of the top cards are its own. Without contention nothing changes.

- Starvation guard: a board whose top card has been ready for 2 hours or more takes the first pick (oldest wait first), so a flood of high priorities on one board cannot hold another board forever. The clock restarts at the board's latest worker start (`task_runs.started_at`), so a head the claim loop keeps skipping (dispatch guard, per-profile cap, claim deferral, no assignee) wins its board at most one starvation pick per 2 hours instead of one on every tick.
- Queue position: the queue line and the 30-minute nudge count cards on other boards that rank ahead and name them: `N card(s) ahead of it (M on other boards sharing these slots)`. The single-board wording is unchanged when nothing elsewhere ranks ahead.

## How it works

New module `hermes_cli/kanban_board_share.py`. `candidates(conn, limit)` lists unclaimed cards this host could lease in `DISPATCH_ORDER` (cards in `dispatch_refusals` are left out) and attaches the head's `ready_since` via `_starve_clock`. `plan` merges the per-board queues into `{board: quota}` in first-pick order (starved heads first, then global rank) and returns `None` when every candidate fits, the host is uncapped or full. `shares` reads each board (one `connect` per board, skipped when boards share one DB file or any read fails) and `run` ticks boards with a quota first, passing an unspent quota to the next board. `_KanbanDispatcher.tick_once` in `gateway/kanban_watchers_dispatcher.py` calls them and passes `spawn_quota` through `tick_once_for_board` to `dispatch_once`; `_tick_spawn_budget` honours it. `count_running_tasks_other_boards` moved into the new module and is re-exported by `kanban_db_dispatch.py`. `ahead_elsewhere` feeds `position()` in `kanban_queue_position.py` (`ahead_other_boards`), rendered by `ahead_words` there and in `kanban_parent_unclaimed._queue_words`. Ranking is advisory; the per-board host cap check stays authoritative. See [kanban-dispatch-fairness.md](kanban-dispatch-fairness.md) and [kanban-dispatcher-live-cap.md](kanban-dispatcher-live-cap.md) for the earlier rotation and cap.

## Code

`gateway/kanban_watchers_dispatcher.py`, `hermes_cli/kanban_board_share.py` (new), `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_parent_unclaimed.py`, `hermes_cli/kanban_queue_position.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/gateway/test_kanban_cross_board_slots.py`. Covered entry paths: the dispatcher tick over two real boards sharing one slot (higher card on the later board starts; a flooded board versus a starved board; a zero quota refuses spawns with a stated reason), `plan` ranking and the no-contention `None`, the starvation clock when the board started nothing versus a guarded head that never starts, and the rendered queue text via `kanban_queue_position` (including the starved head on another board). CLI, tool, REST and Desktop surfaces of the queue line are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The 2-hour starvation window (`STARVE_SECONDS`) is a constant, not configuration.
- Ranking reads every board's DB on each contended tick; an unreadable board only forfeits its quota to later boards. With `HERMES_KANBAN_DB` pinning all boards to one file, ranking is skipped.
- Guards and refusals decided at claim time are not modelled, so a quota can go unspent and passes on.

## Plugin

Core change; no plugin required.
