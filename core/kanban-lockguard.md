# Kanban board connections: OFD lock guard and no per-request init

Patches: 0614, 0616 (daveey/hermes `f2d0e23785`, `070e9c30c8`).

## Behavior

0614: the Kanban dashboard called `init_db` on every request, which dropped the board's `_INITIALIZED_PATHS` entry, so each `/api/plugins/kanban/*` poll re-ran `PRAGMA integrity_check` over the whole file (3-25 s on a 140 MB board), starving the Desktop backend REST API. Requests now rely on `connect()`, which initialises once per process.

0616: Kanban processes were stranded on a deleted `kanban.db-wal`/`-shm` generation (every read failed with "disk I/O error" or reported false corruption until restart; self-heal kanban-759104eb56). POSIX advisory locks belong to the process, so any stray `open()/close()` of the db inside a process cancels SQLite's locks and a sibling then unlinks the WAL. Every board connection now takes the OFD-lock guard `state.db` has had since September, and a stranded cached reader reopens.

## How it works

`hermes_cli/kanban_lockguard.py`: `connect()` is a drop-in `sqlite3.connect` returning a `GuardedConnection` (composable with connect_tracked's close via `guarded_factory`); `guard()`/`unguard()` (needed before leaving WAL); `stranded_sidecars()`/`shm_identity()` detect a stranded process. Only WAL databases are guarded and the guard is lifted before the handle's own close so a true last close still ends the generation. Every `hermes_cli/kanban*.py` opener goes through it. `cached_ro` closes a stale cached reader before reopening it when its `-shm` generation is gone.

## Code

- `hermes_cli/kanban_lockguard.py`, `kanban_db_connect.py`, `kanban_session_activity.py` and the other `kanban_*.py` openers; `plugins/kanban/dashboard/plugin_api.py`.

## Tests

- `tests/hermes_cli/test_kanban_lockguard.py` (9 of 10 fail on the old code, including open_ro, cached_ro and `kbc.connect`; a lint-style test keeps raw `sqlite3.connect` out of `kanban*.py`), `tests/hermes_state/test_wal_lock_guard_lifecycle.py`, `tests/hermes_cli/test_kanban_db.py` (adapted), `tests/plugins/test_kanban_conn_no_reinit.py` (0614).

## Applying

Apply with `git am -3` in series order from the merge-base with upstream; the patch is self-contained apart from the earlier patches it extends. If it no longer applies, re-implement from Behavior and Code.

## Gotchas

- New board openers must use `kanban_lockguard.connect`; the lint test fails otherwise.

## Plugin?

No. It changes core code that runs before or outside plugin loading.
