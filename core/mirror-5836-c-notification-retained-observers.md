# TUI notifications: retained board observers and gone-board release (mirror 5687–5690)

## Behavior
- Notification signatures read through a dedicated query-only retained observer per board instead of cold private connections under reader contention.
- Retained observers are capped (64); a private fallback stays fresh and unpublished; recovery never closes active borrowers.
- Observers for removed boards are released by the existing gone-board handle sweep without blocking later releases.

## How it works
`tui_gateway/notification_reader.py` owns retention and fences memo/connection identity (DB inode plus WAL and SHM identity); `notification_idle.py` interacts with idle retirement; `kanban_gone_board_handles.py` sweeps removed boards using existing `path_gone` semantics and nonblocking acquisition.

## Code
- `tui_gateway/notification_reader.py`
- `tui_gateway/notification_idle.py`
- `hermes_cli/kanban_gone_board_handles.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/tui_gateway/test_notify_reader.py` checks shared/private reader lifetimes, cap eviction, reset during borrow, and WAL/SHM replacement. `tests/hermes_cli/test_kanban_gone_board_handles.py::test_notification_signature_sweep_closes_removed_board_only` checks descriptors; `test_busy_removed_board_does_not_stop_release_of_the_next` checks sweep progress. These exercise reader helpers with SQLite; delivery through a live TUI notification transport is untested.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Generic cached read semantics are unchanged.
- Busy entries are kept for later sweeps.

## plugin?
No. Core change.
