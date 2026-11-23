# Bounded tombstone GC proof

Test many rows sharing the same timestamp, not only distinct timestamps. A tuple `(time,rowid) > (?,?)` against a time-only index can reread the entire tied prefix despite LIMIT. Measure actual late-page runtime cursor rowsRead and query plans; split equal-time rowid and later-time seeks when needed.

Keep row deletion, tombstone bookkeeping, history floor and cursor changes in one transaction. Inject failures at final metadata writes as well as mid-delete. Preserve held rows before consuming bookkeeping, including malformed restriction markers; cursor progress must depend on scanned candidates, not deleted count. Prove a full held batch does not starve later collectible rows and inserts behind the cursor are reached on a subsequent traversal.
