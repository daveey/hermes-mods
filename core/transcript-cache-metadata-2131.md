# Transcript cache metadata without body scans

Mirror ordinal: **2117**.

## Behavior

Desktop's persistent transcript cache separates metadata from transcript JSON. Freshness checks, statistics, key listing and LRU candidate scans read only small metadata rows instead of walking multi-megabyte body overflow pages synchronously on Electron's main thread. Reading an actual transcript still reads and parses its body.

An existing inline-body cache is discarded on first open and recreated empty. This is a rebuildable local cache, not a migration of authoritative session history. Connection/profile/session scoping, body bounds and LRU behavior remain in place.

## How it works

The new `entries` table contains an integer primary key, unique connection/profile/session identity, byte size, saved/used timestamps, activity and message count. `bodies` stores only that ID and JSON text, with a foreign key using `ON DELETE CASCADE`.

`meta`, `stats`, `keys`, LRU ordering and touches operate on `entries`. `read` joins the tables only when text is needed. Writes transactionally upsert metadata with `RETURNING id`, then upsert the body. The catch block rolls back only if SQLite still has an active transaction, avoiding a secondary rollback error masking `SQLITE_FULL` or another auto-rollback failure.

Opening detects the legacy `transcripts` table, closes the connection and unlinks the file plus WAL/SHM companions before rebuilding. It avoids `DROP TABLE` walking the old overflow pages. WAL, normal synchronous mode, the existing busy timeout, foreign-key enforcement and private file mode are configured on the new store. Delete, eviction, scope re-key/replacement and clear preserve body/metadata consistency.

## Code

- `apps/desktop/electron/transcript-cache.ts`: schema split, legacy detection/recreation, metadata-only SQL, joined reads, transactional writes and cascade cleanup.
- `apps/desktop/electron/transcript-cache.test.ts`: schema, large-entry, lifecycle and error regression cases.

## Tests

Source tests were inspected, **not run in this task**. New cases assert that `entries` contains no body and `bodies` has only ID/body; multi-megabyte entries support metadata/statistics without decoding bodies; and drop, drop-everywhere, eviction, re-key over an existing scope and clear leave no orphan bodies. Legacy-cache replacement starts empty/writable with mode `0600`. An injected full database surfaces the real SQLite error and leaves no partial entry.

These structural and behavior tests do not measure live scrolling latency. No Desktop benchmark or Mac UI check was performed; source comments' latency observations are not fresh measurements by this mirror.

## Applying

Use the full snapshot replay in [README.md](README.md), then build/restart Desktop through its normal supported process. In a disposable source checkout, install the lockfile-matched JavaScript workspace dependencies and run the Desktop test runner on `electron/transcript-cache.test.ts`. Do not share a live runtime's `node_modules` to construct proof. The cache rebuild requires no server session migration.

## Gotchas

- The first upgraded open intentionally loses cached transcript bodies; the authoritative server store is unchanged.
- Keep future metadata-only queries off `bodies`; merely avoiding JSON parsing does not prevent SQLite overflow-page traversal.
- Split tables require transactional writes and foreign-key enforcement. Disabling either risks mismatched cache rows.
- Full transcript reads and writes still perform synchronous work. This change is not a promise that all main-thread I/O is eliminated.

## Could this be a plugin?

No for this fix. The Electron cache implementation owns the synchronous SQL and file schema. A plugin can prefetch or display transcripts, but cannot make the existing core metadata queries stop traversing inline bodies.
