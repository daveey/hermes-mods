# Kanban dashboard history: normalized rows, retained reductions and bounded warm refresh

Mirror snapshot 4352; ordinals 4175, 4176, 4177, 4178.

Entries:

- **4175**: Normalize dashboard history and honor P2 event invalidation
- **4176**: Retain pure dashboard history reductions with exact run overlays
- **4177**: Bound dashboard warm refresh to changed history
- **4178**: boundary baseline: board_read imports UNREAD_EVENT_KINDS

## Behavior

The board dashboard recomputes per-card history on refresh; these entries make that proportional to what changed.

- **Normalized history and P2 invalidation** (4175): immutable keyed history rows avoid repeated column scans; trusted P2 change signals invalidate in-place event edits while bookkeeping-only rows reuse cached events. Full diagnostics stay fresh.
- **Retained reductions** (4176, 4177): ready-floor, terminal-provider, latest-block and latest-kind reductions are retained per immutable event history with type-exact run keys. Trusted P2 refreshes detect only the unmarked heartbeat inserts via kind/task index seeks instead of counting every displayed task's history; legacy refreshes keep whole-history count/max derived from the events actually retained. Retained runs compare ids and bookkeeping through a quoted SQL signature and merge type-exact updates.
- **Boundary baseline** (4178): `board_read` importing `UNREAD_EVENT_KINDS` from the activity-revision module (the same tuple its trigger excludes) is registered as an intended crossing in the boundary lint baseline.

## How it works

- `plugins/kanban/dashboard/board_read.py`, `board_incremental.py` and the new `board_run_history.py` hold the retained history and run overlays; `hermes_cli/kanban_diagnostic_history.py` (new) and `kanban_diagnostics.py` hold the pure history reductions; `scripts/boundary_lint_baseline.json` carries the new baseline entry.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_diagnostic_history.py`
- `hermes_cli/kanban_diagnostics.py`
- `plugins/kanban/dashboard/board_incremental.py`
- `plugins/kanban/dashboard/board_read.py`
- `plugins/kanban/dashboard/board_run_history.py`
- `scripts/boundary_lint_baseline.json`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/plugins/test_kanban_board_history.py`
- `tests/plugins/test_kanban_event_marks.py`
- `tests/plugins/test_kanban_history_ready_reductions.py`
- `tests/plugins/test_kanban_history_reductions.py`
- `tests/plugins/test_kanban_run_history.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- The commit messages describe these as stages of a CPU-reduction effort, not full budget acceptance (scale, replay and live profiles were still pending).
- Results must equal the unreduced computation; the shipped tests compare against that.

## Plugin?

Applies to the bundled kanban dashboard plugin (`plugins/kanban/dashboard`); it is part of core, no separate enable step.
