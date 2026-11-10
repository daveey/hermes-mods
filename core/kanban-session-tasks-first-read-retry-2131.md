# Session-tasks first read outlasts a busy board

Patches **2102, 2103**.

## Behavior

- 2102: `/api/session-kanban-tasks` no longer returns 500 when a board's lifecycle lock is held past the 500 ms read budget and no previous list exists for that query (a chat opened for the first time since backend start; Desktop showed "Task status unavailable"). The busy first read is retried for up to 3 s; a board held longer still raises (unknown, never "no work"). A board with a prior list still returns it with rows marked `stale`.
- 2103 (review follow-up): the 3 s budget is one deadline per `session_tasks` request shared by every board, so several cold busy boards cannot stack past the Desktop's 15 s timeout. Calls with no deadline (the links/duplicate projection, which holds a lock) never retry. Instant busy errors sleep 50 ms to avoid spinning.

## How it works

`kanban_session_activity_busy.session_tasks(path, ids, statuses, read, *, deadline=None)` loops: non-busy errors raise; busy with a memoised list returns stale rows; otherwise sleeps until `deadline` (from `request_deadline()`, `FIRST_READ_BUSY_BUDGET_S`). `kanban_session_activity.session_tasks` creates the deadline once.

## Code

- `hermes_cli/kanban_session_activity_busy.py`, `hermes_cli/kanban_session_activity.py`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `tests/hermes_cli/test_kanban_busy_board_activity.py`: first read waits out a hold longer than one read budget; held board with no previous read is still an error, not an empty list. `tests/hermes_cli/test_kanban_busy_first_read.py` (fake clock): retries until the board frees; no deadline never retries; non-busy not retried; instant busy pauses; several boards share one budget; request shares one deadline; links projection never waits. The Desktop panel itself is untested.

## Applying

Apply ordinal(s) 2102, 2103 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. Extends [kanban-busy-board-activity-2048.md](kanban-busy-board-activity-2048.md); keep that patch's memo and stale-marker behaviour. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

A hold longer than 3 s still fails the request. The memo is per process, so each restart repeats the cold-read exposure.

## Could this be a plugin?

No. It is core Kanban session activity read code behind a REST route.
