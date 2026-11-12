# Offline SQLite file access reserves a path instead of holding the registry lock

Patches: **2702–2707**.

## Behavior

- `offline_file_access(path)` (raw reads, copies and restores of a database file) used to hold the process-wide SQLite connection registry lock for the whole raw I/O. Any tracked connect or close in the process, on any database, waited behind it; an idle Kanban board reader's `close()` could be pinned by an unrelated file preview.
- Now it **reserves the target path** and releases the registry lock for the body. Unrelated tracked opens and closes proceed. A tracked open of the same file (including its `-wal` and `-shm` sidecars) waits until the reservation ends; a non-blocking open (`wait_for_tracking=False`) refuses with `sqlite3.OperationalError` carrying `SQLITE_BUSY`.
- Targets that cannot be predicted fail closed. A `file:` URI with an escaped NUL, a `vfs=` parameter, or a non-local authority, any path that cannot be resolved, and any custom `connect_fn` without an explicit `tracking_path` conflict with every active reservation. After the open, the file SQLite actually opened is re-checked; if it is reserved, the handle is closed and the open refused before any statement runs.
- A same-thread nested reservation, or a caller that already holds the registry lock, is refused instead of waiting on another owner (`LiveConnectionError` for raw access, `SQLITE_BUSY` for opens), so no owner is pinned.
- `read_header_bytes_preopen` waits for a foreign reservation on the file (as it used to wait for the registry lock) and then reads. When the caller owns a reservation or holds the registry lock it returns `None`.
- `track_connection` (manual registration) also waits for a reservation on its path.
- The live-connection refusal text of `offline_file_access` is unchanged, including the `its main database <path>` wording for sidecar targets (2705); the nested-reservation refusal now names the path.
- `SessionDB`'s writer open names its target (`tracking_path=self.db_path`), so it waits only on reservations of `state.db` and cannot be starved by overlapping reservations on other files (2707).

## How it works

- 2702: new `hermes_cli/sqlite_offline_reservations.py` holds a list of `(key, thread id, Event)` reservations. `database_key` canonicalizes a path or `file:` URI (`None` for in-memory), `overlaps` treats `-wal`/`-shm` as the same file, `conflicts` returns active reservations for a key (raw callers ignore their own thread's), and completion is signalled by an `Event`, not by reacquiring the registry mutex. `sqlite_safe_read.offline_file_access` checks live connections and publishes the reservation under `_live_lock`, then runs its body unlocked and sets the event in `finally`. `_tracking_access` (used by `connect_tracked` and `track_connection`) rechecks reservations under the lock and waits on the event outside it.
- 2703: `database_key` returns an `UNKNOWN` sentinel for unpredictable targets; `connect_tracked` treats it, or `connect_fn` without `tracking_path`, as conflicting with everything, and compares the resolved opened path with the predicted key. `read_header_bytes_preopen` is restructured (`_read_header_registered`) to wait outside the lock.
- 2704: comment-only. Callers (`agent/context_references.py`, `hermes_cli/backup_restore.py`, `hermes_cli/session_recovery.py`, `hermes_cli/web_routers/files.py`) now describe a path reservation, and the module docstring records that reservations are keyed by resolved path like the live registry, so a hardlink alias or an ATTACHed database is not recognised as the reserved file.
- 2705: restores the original refusal wording that a files preview route test asserts.
- 2706: test-only. Two finalizer/exit tests used `offline_file_access` as a stand-in for a registry-lock holder; they now hold `_live_lock` directly.
- 2707: `hermes_state.SessionDB` passes `tracking_path`.

## Code

`hermes_cli/sqlite_offline_reservations.py` (new), `hermes_cli/sqlite_safe_read.py`, `hermes_state.py`, `agent/context_references.py`, `hermes_cli/backup_restore.py`, `hermes_cli/session_recovery.py`, `hermes_cli/web_routers/files.py` (comments only).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_sqlite_offline_reservations.py`, `tests/hermes_cli/test_sqlite_reservation_adversarial.py`, `tests/hermes_cli/test_kanban_tracking_contention.py`, `tests/hermes_cli/test_kanban_reader_close_isolation.py`, `tests/hermes_cli/test_sqlite_safe_read_finalizer.py`. Entry path: `sqlite_safe_read.offline_file_access`, `connect_tracked`, `track_connection` and `read_header_bytes_preopen` called directly from threads, plus `owner.connect_with_identity` and `lockguard.connect` for the Kanban board reader (an idle reader's close, a failed open and recovery are not held up by an unrelated reservation); `SessionDB(...)` open under an unrelated reservation (2707). Covered cases include plain, `file:` URI and symlink aliases of the reserved file with `-wal`/`-shm` suffixes, NUL-truncated URIs, a factory substituting a reserved path (the refused handle is closed), nested and recursive-lock refusals, and the header probe waiting for a reservation. The real `files` preview route, backup restore and session recovery callers are **untested** here by this group (2704 is comments only); Windows path handling is **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Reservations are keyed by resolved path like the live registry: a hardlink alias or an ATTACHed database is not recognised as the reserved file.
- A raw body no longer excludes unrelated tracked closes. Code that used `offline_file_access` as a general registry mutex must hold `_live_lock` itself (as 2706 does in tests).
- A reservation overlaps with every unpredictable open, so a tracked open without a known target can wait as long as any reservation lasts. Pass `tracking_path` when the target is known (2707 does so for `state.db`).
- On Windows (`os.name == "nt"`) every `file:` URI is treated as unpredictable.

## Plugin

Core change; no plugin required.
