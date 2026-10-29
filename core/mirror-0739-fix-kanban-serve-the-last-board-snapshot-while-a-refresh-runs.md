# fix(kanban): serve the last board snapshot while a refresh runs [daveey]

Patch 0739, source `11fd6953d877dae7c4411edeb88f372bca4ddef8`.

## Behavior

fix(kanban): serve the last board snapshot while a refresh runs [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `+"""Last-snapshot reads for GET /board.`
- `import json`
- `def _managing_sessions(conn: sqlite3.Connection) -> dict[str, str]:`
- `def get_board(`
- `-"""Kanban dashboard plugin: GET /board reuses its last result while the board's files are unchanged.`
- `def _create(title: str) -> None:`
- `def test_unchanged_board_is_read_once_and_a_write_is_seen(board):`
- `def test_query_params_are_cached_separately(board):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Desktop polls GET /board continuously. A kanban commit moves the db/-wal
> signature, so the 15s cache misses and the poll waited out the uncached
> read (about 1.3s, tens of seconds under load) and hit the client timeout.
> Return the last snapshot and refresh off the request.
>
> Problem: since 2026-10-28 00:56 PDT, after b549dbc49c, desktop.log still
> showed "Timed out connecting to Hermes backend" on GET kanban/board.
> Why 1: the poll waited for the uncached read. coalesced_read makes every
> waiter await the same flight.
> Why 2: every commit changes the wal signature, so a busy board almost
> never hits the cache.
> Why 3: the annotation-flight fix removed one lock but left the refresh
> on the request path.
> Root: a poll treats a refresh as part of the request.
> Not caught: tests proved cache hits and pool admission, not that a poll
> returns while the refresh is blocked.

## Code

- `plugins/kanban/dashboard/board_snapshot.py`
- `plugins/kanban/dashboard/plugin_api.py`

## Tests

Source test files changed by this commit:

- `tests/plugins/test_kanban_board_read_cache.py`

New or changed test definitions present in the source diff:

- `test_poll_returns_last_snapshot_while_refresh_is_blocked`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
