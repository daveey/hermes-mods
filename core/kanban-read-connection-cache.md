# Reuse read-only Kanban connections

Patch 0521 (source `f9e611f5db`).

## Behavior

Repeated linked-card reads reuse a connection per board or replica rather than reopening SQLite files.
New committed changes remain visible, and idle readers do not block WAL checkpoints.

## How it works

`cached_ro()` serializes access with a per-connection lock, opens read-only, and reopens on file replacement
or SQLite errors. Both session activity and replica-card queries use it. This is backend code, not a plugin.

## Code

- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_chat_links.py`

## Tests

`tests/hermes_cli/test_kanban_ro_cache.py` and `test_kanban_chat_links.py`: 16 passed in an isolated worktree.
Covers reuse, fresh commits, replacement, errors, missing files, nonblocking checkpoints, thread sharing,
and replica link additions/releases. No live performance benchmark repeated for this mirror.

## Applying

Apply 0521 with `git am -3` after the preceding fork patches.

## Gotchas

Connection lifetime belongs to the backend process; deploying the source alone does not reload a running backend.
