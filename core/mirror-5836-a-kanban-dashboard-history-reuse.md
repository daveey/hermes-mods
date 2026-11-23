# Kanban dashboard: retain exact event histories across task-only changes (mirror 5530, 5531)

## Behavior
- Task-wide change marks no longer discard immutable event histories when only task content, runs, comments or neighbours changed. `_same_events` compares every ordered `task_events` scalar (and column names and storage types, via `marshal`) before retaining immutable history reductions, so same-ID edits are caught; it still scans the SQL history but avoids rebuilding/reducing it and the broad loader's duplicate run/graph reads. A changed history reloads.
- 5531: when the stored event mark already differs from the current one (append/delete), the exact comparison is skipped; equal marks still get the exact comparison.

## How it works
`plugins/kanban/dashboard/board_read.py`.

## Code
`plugins/kanban/dashboard/board_read.py`.

## Tests
Source: `tests/plugins/test_kanban_board_history.py` (touched; no new named test) and `tests/plugins/test_kanban_materialization_reuse.py` (content-only edit retains history and visible payload; exact comparison rejects each mutation (parametrized); exact empty history with an independent task; a changed event mark skips the exact scan). Not executed in this mirror pass.

## Applying
Apply in order.

## Gotchas
Marked tasks with equal marks still cost one SQL history scan; no timing measurement is in the patches.

## plugin?
Bundled kanban dashboard plugin file; not a standalone plugin.
