# Kanban board switcher counts stay off writer initialization

Patch: `patches/1559-perf-kanban-keep-cold-board-counts-off-writer-initia.patch` (`b4f69efc705`, `perf(kanban): keep cold board counts off writer initialization`).

## Behavior

- `GET /api/plugins/kanban/boards` returns every board with `counts` (`{status: n}`). Before, each count opened the board through the writer connect (`kbc.connect`), which runs first-init integrity probes, schema reconciliation and migrations and takes the cross-process init lock, once per board on a cold start.
- Now each count reads `SELECT status, COUNT(*) FROM tasks GROUP BY status` over a read-only, `query_only` connection from `hermes_cli.kanban_generation.open_ro`. It never creates a missing board file, accepts legacy boards whose `tasks` table has only the old columns, and returns `{}` on `OSError`, `ValueError` or `sqlite3.Error`. The connection is closed after the read.
- `open_ro` tries `mode=ro` first and retries `mode=rw` (still `query_only`) only when the WAL sidecars are missing.
- Board prewarm at boot now warms the active board, then the default board, then every other board whose count shows at least one `blocked` card (Desktop's blocked-task loader polls those boards too). Boards with no blocked cards are not warmed.
- Each warmed board logs `kanban board prewarm finished board=<name> elapsed_s=<s>` at info. A failed warm now logs a warning (was debug). If board discovery (`list_boards` or a count) raises, a warning is logged and the active/default warm-ups already done are kept.

## How it works

`plugins/kanban/dashboard/board_counts.py` (new) defines `board_counts(slug)`; `plugin_api._board_counts` is now that function. `board_prewarm._warm_names(api)` yields the current board and `DEFAULT_BOARD` first, and only then runs discovery, so a discovery failure cannot stop the guaranteed warm-ups. The existing `seen` set dedupes boards that resolve to the same snapshot key. Writer connect still owns integrity checks and migrations when a board is actually opened for a full read or write.

## Code

`plugins/kanban/dashboard/board_counts.py` (new), `plugins/kanban/dashboard/board_prewarm.py`, `plugins/kanban/dashboard/plugin_api.py`.

## Tests

From the patch; not run for this mirror. `tests/plugins/test_kanban_cold_start_reads.py` (6):

- `test_board_list_counts_do_not_initialize_writers` (the `/boards` route path)
- `test_counts_accept_legacy_status_only_schema_and_do_not_create`
- `test_counts_connection_is_query_only_and_closed`
- `test_prewarm_covers_blocked_board_poll_without_warming_every_board`
- `test_failed_prewarm_is_observable`
- `test_discovery_failure_preserves_default_warmup`

The `mode=rw` fallback for missing WAL sidecars lives in `open_ro` and is not exercised by these tests.

## Applying

Depends on the preceding series (it uses `hermes_cli.kanban_generation.open_ro` and the board snapshot prewarm from earlier patches). Apply the full ordered series per the README "Applying" section; standalone application is not certified.

## Gotchas

- A corrupt or unreadable board shows `{}` counts rather than an error; the integrity check only runs when the board is opened for real.
- Discovery runs a count per board at boot. That is cheap now, but on a host with many boards it is still one read-only open per board.

## Plugin?

Already a plugin: the code is in the bundled Kanban dashboard plugin (`plugins/kanban/dashboard`), using core `hermes_cli` helpers.
