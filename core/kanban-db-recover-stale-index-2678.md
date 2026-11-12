# Kanban DB recovery: stale-index lookups key on inserted rows

Patches: **2565, 2588**.

## Behavior

- When `kanban.db` recovery salvages a table whose leaf page is damaged, a garbage row returned by a scan can no longer hide a deleted row's stale index entry. Before, such a row (arbitrary rowid, fails `NOT NULL`, dropped on insert) could carry the rowid of a deleted row that an index still listed; that rowid's point lookup was skipped, never marked stale, and the backup fill brought the deleted row back.
- `recovered` in the recovery report counts rows actually inserted, not rows the scan returned.
- 2565 is test-only: no runtime behavior change. The test helper `_damage_table` now overwrites the whole leaf page, header included (page type byte `0xff`), so every read of it fails with `SQLITE_CORRUPT`. The old helper kept a valid leaf header over garbage cells, whose pointers ran past the page buffer, so some reads returned rows with random rowids depending on heap state (a CI flake). The commit message measured garbage rowids salvaged in 186/360 runs before and 0/360 after.

## How it works

- 2588: `_salvage_table` in `hermes_cli/kanban_db_recover.py` now takes the destination connection, inserts the scanned rows itself (`INSERT OR IGNORE`), then reads the rowids `dst` actually holds and point-looks-up every listed rowid that `dst` lacks. A clean hit is inserted; a clean miss (no row, no read error) is marked stale and removed from the index listing. It returns the inserted count, columns, failed read paths, listed rowids and the stale set. `_build` drops its own insert block and records `loss.recovered = inserted`.

## Code

`hermes_cli/kanban_db_recover.py`, `tests/hermes_cli/test_kanban_db_recover.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_db_recover.py`. Entry path: `kanban_db_recover.recover(path, file_card=False, reconcile_sync=False)` on a generated board with a backup copy.

- 2565: the existing `test_stale_index_entries_of_deleted_rows_are_not_filled_back_in` uses the new whole-page damage helper.
- 2588: `test_garbage_scan_row_with_a_deleted_rowid_does_not_hide_its_stale_index_entry` monkeypatches `_read_rows` to append an all-NULL row with a deleted rowid to the plain scan, then asserts the deleted row stays gone, the recovered table equals the pre-damage one minus the lost page, `stale_index_entries` covers the deletions, and `recovered` excludes the dropped row.

The `hermes kanban` recovery CLI and automatic recovery triggers are **untested** here; only `recover()` is driven.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- `_salvage_table` has a new signature (`dst` argument, inserted count instead of a rows dict); out-of-tree callers must adapt.
- The damage helper is deliberately destructive and only for test copies of a board.

## Plugin

Core change; no plugin required.
