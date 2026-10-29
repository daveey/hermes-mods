# Desktop wake burst and board snapshot cost

Patches:

- `patches/0983-perf-backend-stop-a-Desktop-wake-burst-queueing-on-t.patch`
- `patches/1013-perf-kanban-throttle-GET-board-snapshot-refresh-to-o.patch`

A wake burst of session, kanban and status requests ran 6-9 s on a loaded Mac and tripped the Desktop's 5 s timeouts; a busy board re-read cost about 1.3 s CPU per poll.

## Behavior

`/api/status` caches the `_config_version` parse on the file signature (errors still re-read, warn and raise); `stale_ok` reaches `_ledger_owners` so the session strip no longer waits on the links lock; `GET /board` refreshes its snapshot at most once per 20 s.

## How it works

Caching and a parameter pass-through; no API change.

## Code

`hermes_cli/config_version_stamp.py`, `kanban_chat_links.py`, `kanban_session_activity.py`, `plugins/kanban/dashboard/board_snapshot.py`.

## Tests

`tests/hermes_cli/test_config_version_stamp_cache.py`, `test_kanban_chat_links.py`, `tests/plugins/test_kanban_board_read_cache.py`.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.
