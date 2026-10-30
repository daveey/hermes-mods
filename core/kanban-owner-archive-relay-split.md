# Move the kanban_archive worker flow into kanban_owner_archive_relay

Patch: 1289.

## Behavior

No behavior change. Fork PR #318 merged with the code-health ratchet red (`FILE_LINES` grew in `kanban_db.py`, `kanban_db_dispatch.py` and `tools/kanban_tools.py`; `_run_reclaim_phase` gained a blind `except`). The `kanban_archive` worker flow (forward, scope rule, remote stop) and the dispatcher's two remote-archive appliers move into `kanban_owner_archive_relay` as `worker_archive` and `apply_remote_archives`, so all three files end at or below their pre-#318 size.

## Code

`hermes_cli/kanban_owner_archive_relay.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_db_dispatch.py`, `tools/kanban_tools.py`.

## Tests

No new tests; the existing archive-relay tests cover the moved code. This mirror task did not execute them.

## Applying

Apply 1289 after the series; it is a pure move.

## Gotchas

If a newer core already trimmed these files, skip it. No plugin involved.
