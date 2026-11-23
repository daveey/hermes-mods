# Kanban sync and dashboard read-cost reductions (mirror 5711, 5714, 5724, 5735)

## Behavior
- Board-watch path handling avoids resolving every parent twice per read while keeping symlink/`..` semantics and fresh target identity.
- The sync loop's run census is answered from kept BoardWatch readers and reused while data_version is unchanged; other collectors keep fresh reads and fall back to `active_card_runs` when needed.
- Lane projection batches event-history reads (chunks of up to 400 card ids on first demand, selected-source/request-local rows only).
- Dashboard task-only invalidations batch ambiguous history comparisons into one IN query on short-lived snapshots.

## How it works
`kanban_sync_board_watch.py`, `machine_report_census.py`, `machine_metrics.py`, `kanban_lanes_flow_evidence.py`/`kanban_lanes_sources.py`, and `plugins/kanban/dashboard/board_event_history.py`, `board_read.py`, `board_rows.py`.

## Code
- `hermes_cli/kanban_sync_board_watch.py`
- `hermes_cli/machine_report_census.py`
- `hermes_cli/kanban_lanes_flow_evidence.py`
- `plugins/kanban/dashboard/board_event_history.py`
- `plugins/kanban/dashboard/board_read.py`
- `plugins/kanban/dashboard/board_rows.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_kanban_sync_watch_paths.py` checks no warm realpath walk and symlink retargeting. `test_machine_report_census.py::test_only_the_reporters_own_collect_uses_the_kept_census` checks scope; `test_unchanged_boards_are_not_read_again_and_one_write_rereads_one_board` checks query reuse. `test_kanban_lanes_event_batch.py` checks batching by selected source and failed-batch nonpublication. `tests/plugins/test_kanban_event_batch.py::test_payload_bytes_match_scalar_oracle` checks parity; `test_kanban_board_reader_lifetime.py` checks snapshot closure. Live dashboard rendering and production performance are untested.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- These bound query counts; the source does not claim demonstrated CPU or latency wins.

## plugin?
Partly: the dashboard files live in the bundled kanban plugin; the rest is core.
