# Session change watcher reads a transactional projection revision

Patches:

- `patches/1552-fix-watcher-read-transactional-session-projection-re.patch` (`ce01f093fca`, `fix(watcher): read transactional session projection revision instead of full digest`)
- `patches/1553-fix-watcher-seed-unique-projection-generation-atomic.patch` (`deacf9f2515`, `fix(watcher): seed unique projection generation atomically on installation`)

## Behavior

- The TUI gateway change watcher decides whether to emit `sessions.changed` for `state.db`. Before, any commit to the file (for example the per-minute `gateway_heartbeats` write) made it re-digest the 22 Desktop-projection columns of every `sessions` row, throttled to once per 3 s (`SESSION_DIGEST_MIN_S`).
- Now an upgraded store carries a write-side generation in `state_meta.sessions_change_seq`. SQLite triggers replace it with a fresh random 16-byte hex value inside the same transaction as any session insert, delete, or an update that changes one of the projection fields. The watcher reads only that value after a commit, so unrelated commits (heartbeats, other tables, equal-value updates, heartbeat-only columns such as `last_activity_at`) cost two small probes and emit nothing.
- A rolled-back session write leaves the generation unchanged, because the trigger write rolls back with it.
- The value is a random generation, not a counter: an in-place restore can replace a database with different content after the same number of writes.
- 1553: installing or replacing the triggers seeds a fresh generation in the same write transaction. Two stores upgraded from a pre-trigger release, with no later session writes, therefore never share an initial value, so restoring one over the other in place (same inode) is still seen. A store with the triggers but no generation row reads as "no capability" and falls back to the content digest, never a shared `0`.
- A settled reopen of an already-installed store writes nothing (no schema change, no `data_version` move).

## How it works

`hermes_state_projection.py` (new) holds `SESSION_SIGNATURE_FIELDS` (shared by the watcher and the triggers), the three trigger bodies `sessions_projection_{insert,delete,update}` (the update trigger fires `AFTER UPDATE OF <fields> WHEN OLD.f IS NOT NEW.f OR ...`), `session_projection_installed(conn)` (all three exist with exactly the current SQL) and `session_projection_revision(conn)` (the generation, or `None`).

`hermes_state_schema.py` calls `_ensure_session_projection` at startup: a read probe returns early on a settled store; otherwise it takes `BEGIN IMMEDIATE`, probes again (a sibling process may have installed first), installs the triggers and seeds the generation, then commits.

`tui_gateway/change_watcher.py::_session_db_content_sig` keeps the stat-only fast path for a settled, unchanged mtime. After a commit it opens a read-only connection, runs `BEGIN`, and reads the revision; a hit returns `("revision", (st_dev, st_ino), generation)`, which skips the 3 s digest throttle. The file identity is tracked per path, so a replaced file drops the cache even if its generation happened to match. Stores without the full trigger capability keep the old column digest.

## Code

`hermes_state_projection.py` (new), `hermes_state_schema.py`, `tui_gateway/change_watcher.py`.

## Tests

From the patches; not run for this mirror. `tests/tui_gateway/test_session_projection_revision.py`:

- `test_settled_unchanged_tick_opens_no_database`, `test_projection_reads_are_bounded_after_unrelated_commits`
- `test_every_projection_field_invalidates_on_next_tick` (parametrized over the fields), `test_insert_delete_and_rollback`, `test_heartbeat_and_equal_updates_do_not_write_counter`
- `test_first_install_seeds_generation_and_settled_open_is_read_only` (renamed in 1553), `test_upgrade_populated_store_without_counter`
- `test_replacement_with_equal_counter_is_not_cached`, `test_in_place_restore_of_equal_count_store_invalidates`, `test_divergent_upgraded_stores_restore_in_place_invalidates` (1553)
- `test_failed_read_retries_without_throttle`, `test_missing_trigger_falls_back_to_content`, `test_installed_triggers_without_generation_fall_back_to_content` (1553)

`tests/hermes_cli/test_browse_schema_drift.py` and `tests/hermes_state/test_hermes_state.py` now drop `sessions_projection_update` before `ALTER TABLE ... DROP COLUMN` in their fixtures. The race between two processes both installing (the second `BEGIN IMMEDIATE` probe) has no dedicated test.

## Applying

Depends on the preceding series. Apply the full ordered series per the README "Applying" section; standalone application of these two patches is not certified.

## Gotchas

- SQLite refuses `DROP COLUMN` on a column a trigger references. Any migration or tool that drops a projection column from `sessions` must drop `sessions_projection_update` first (startup reinstalls it).
- Adding a field to `SESSION_SIGNATURE_FIELDS` changes the trigger SQL; the exact-SQL capability probe then fails until the next startup reinstalls (and reseeds), and the watcher uses the content digest in between.
- Heartbeat columns stay out of the signature on purpose; liveness comes from `session.active_list`.

## Plugin?

No. The triggers live in the core session schema and the watcher is core TUI gateway code.
