# Kanban readers: one row per probe, only rows that can win

Mirror snapshot 5923; ordinals 5895, 5896.

Entries:

- **5895**: perf(kanban readers): hand Python one row per probe and only the rows that can win
- **5896**: kanban table columns: read through execute only, so connection wrappers keep working

## Behavior

- In a many-thread single-process backend every statement and every result row Python sees costs GIL hand-offs, so reader cost is statements plus rows, not SQLite time. These entries cut both for the Kanban session/board readers without changing their output (the source compared 21 reader calls on a board snapshot and pins old answers against reference copies of the old readers on random boards):
  - **Schema probes**: `PRAGMA table_info(tasks)` returns one row per column. `hermes_cli/kanban_table_columns.py` reads the same names with `pragma_table_info` + `json_group_array`, one statement and one row for any number of tables, resolved in the caller's snapshot with PRAGMA name resolution (temp, main, attached). There is deliberately no memo, so no key can miss a migration, rebuilt replica or swapped file.
  - **Run models**: `add_run_models` joins each card's newest run to its route/terminal events in SQL instead of reading every run's events and keeping the newest in Python.
  - **Card details**: one query returns only the comment/run-summary rows that can still win the "newest note" and "PR link" selection (rows at or after the newest surely non-blank text, plus rows that may name a PR, in the old offer order; non-numeric times keep all of a card's rows).
  - **Blocked-upstream walk**: each parent's edges, status and authority are read once per call instead of once per card.
  - **External watch hosts**: the newest id and new watch payloads come back as one JSON array (BLOB payloads fall back to the old row loop).
- 5896: the columns helper now reads through `conn.execute` only (no explicit cursor, no `row_factory` juggling), so connection-like wrappers that expose only `execute` keep working; the single row is read positionally whatever the row factory.

## How it works

- Callers switched to the helper include the session-task, listed-local-task, count, parked, status-filter, remote-link, wait-row, not-watch, placement and run readers (`kanban_session_activity.py`, `kanban_session_outcome_tasks.py`, `kanban_session_wait_rows.py`, `kanban_watch.py`, `kanban_external_watch.py`, `kanban_primary_execution.py`, `kanban_replica_execution.py`, `kanban_run_machine.py`, `kanban_run_model.py`, `kanban_session_activity_card_details.py`).

## Code

Shipped non-test paths:

- `hermes_cli/kanban_table_columns.py` (new)
- `hermes_cli/kanban_external_watch.py`
- `hermes_cli/kanban_primary_execution.py`
- `hermes_cli/kanban_replica_execution.py`
- `hermes_cli/kanban_run_machine.py`
- `hermes_cli/kanban_run_model.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_activity_card_details.py`
- `hermes_cli/kanban_session_outcome_tasks.py`
- `hermes_cli/kanban_session_wait_rows.py`
- `hermes_cli/kanban_watch.py`

## Tests

Source-side test shipped by these entries; not executed by this documentation pass, so no pass result is claimed here:

- `tests/hermes_cli/test_kanban_reader_hot_rows.py` (columns match the pragma in one statement, ignore the row factory, follow schema changes and file swaps, resolve temp/main/attached; run models match the old reader and read only the newest run's events; notes and PR links match the old reader on random boards; a long comment thread hands out only rows that can win; no-comments-table fallback; hosts match the old incremental reader, restart on a smaller board, fall back on BLOB payloads and keep all-or-nothing failure; listed-card readers probe each schema in one row; upstream walk matches the old walk and reads shared ancestors once)
- 5896 adds no test file of its own.

## Applying

These entries are part of the ordered snapshot 5923. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying) (`git am -3 --empty=keep` over the full ordered list, plus any named repair overlay). They are not independent patches.

## Gotchas

- Do not add a schema-columns memo; it would still need a statement to validate its key and risks stale columns after a migration or replica rebuild.
- Wrappers around connections must expose `execute`; the helper no longer opens cursors.
- Timings quoted in the source commit are single-machine, noisy measurements, not acceptance numbers.

## Plugin

Core change; no plugin or hook is involved.
