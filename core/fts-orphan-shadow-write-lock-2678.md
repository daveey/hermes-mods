# Orphan FTS shadow repair re-probes under the write lock

Patches: **2564**.

## Behavior

- Several processes opening a fresh `state.db` at once no longer risk dropping a live FTS5 family. Before, a sibling's `CREATE VIRTUAL TABLE` could commit between the opener's two probes; the opener then treated the just-published family as orphaned and dropped its shadow tables. Every later read of that index failed with `vtable constructor failed: messages_fts_trigram`.
- A real orphan (shadow tables present, virtual table row absent) is still dropped and rebuilt from `messages`, with the same warning log.
- A healthy family is left untouched; when the repair owns the transaction it ends committed (no transaction left open).

## How it works

- `_orphan_fts_shadows(cursor, family)` runs the two probes: virtual table row absent, then which of the `_content/_data/_docsize/_idx/_config` shadows exist. It returns `[]` when the vtable is live.
- `_drop_orphan_fts_shadow_tables` probes once without a lock to skip healthy families cheaply. For a candidate it opens `BEGIN IMMEDIATE` (only when the connection is not already in a transaction; a caller's open transaction already pins one snapshot), re-probes, drops the shadows, and commits. Any exception rolls back when it owns the transaction and is re-raised.

## Code

`hermes_state_fts.py`, `tests/hermes_state/test_fts_orphan_shadow_repair.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_state/test_fts_orphan_shadow_repair.py`. Entry path: calls `_drop_orphan_fts_shadow_tables` directly with a cursor proxy that commits a sibling's `CREATE VIRTUAL TABLE messages_fts_trigram` between the two probes, then checks nothing was repaired, no transaction is open, `PRAGMA integrity_check` is `ok`, and the shadow tables survive. The multi-process race itself and the `SessionDB` open path are **untested** here; they are only modelled by that proxy.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The `BEGIN IMMEDIATE` waits on the database write lock, so a repair candidate can block behind another writer for the connection's busy timeout.
- The first, lock-free probe is only a filter; the decision is the re-probe under the lock.

## Plugin

Core change; no plugin required.
