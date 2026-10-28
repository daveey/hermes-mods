# Instant session loads: local transcript cache

Patches 0532 (source `307ab56d4b`), 0533 (source `8914c58a90`) and 0548 (source `0c02a4eb07`). Part C1 of the "instant session loads"
plan; part A (sidebar rows from the hub replica) is [desktop-sidebar-replica-live.md](desktop-sidebar-replica-live.md).

## Behavior

- **Click paints from a local cache.** Desktop keeps a SQLite file in its userData (`transcript-cache.sqlite`)
  with the raw `/api/sessions/{id}/messages` rows of recently active sessions on every registry gateway, keyed
  by (connection, profile, stored session id): the latest 200 rows plus older pages the user loaded. A sidebar
  click paints the newest 120 cached rows (the page the open's REST read fetches) in the same tick.
- **Prefetch.** Electron main reads the local hermes-hub replica (`GET /api/plugins/hub-sync/sessions`) every
  5 s and keeps, per gateway, the 200 most recently active session tips current (people's chats before
  automations). A session is refetched when its `activity` or `message_count` moved, at most every 30 s while
  it keeps moving. Gateway names map to registry connections through each connection's own
  `/api/plugins/hub-sync/status`, remembered in the cache file. A gateway whose route fails is skipped for 60 s.
  Remote gateways are dialed passively (0548): prefetch rides the pool entry the remote prewarmer holds and never
  starts a cold SSH dial or spawn, bumps `lastActiveAt`, or records a profile as opened. The local backend may start.
- **Open and reconcile.** When the clicked row names its owner, the REST read starts at the click (not after
  the owner lookup and gateway switch) and the cold resume reuses that same request. The fresh page replaces
  the cached paint display-only; an unchanged session keeps the painted array (no re-render, no flicker), and a
  changed one shows each row once.
- **Asleep machine.** A sidebar row whose gateway is unreachable (`offline_cached`) opens read-only from every
  cached row and never dials the gateway. A send shows "<gateway> is asleep, so this chat is read-only…" and
  keeps the draft. A row with nothing cached is refused with the old offline notice.
- **Timing log.** One `[open-timing] {…}` line per open in the Desktop log: `paintMs` (click to first paint,
  `paint` = cache / warm / rest), `stages` (owner, gateway, rest), `reconciledMs`, `changed`, `outcome`
  (rest / warm / asleep / failed / abandoned). The last 200 stay on `window.__hermesOpenTimings`.
- **Server paging.** `get_messages(include_ancestors=True)` on a multi-segment compression lineage pages in SQL
  on the stored `display_identity` instead of reading every row into Python. Rewind reads and read-only stores
  that cannot backfill identities keep the Python fold.
- The gateway switch no longer wipes the cache: entries are connection-scoped, so another machine's same-named
  id cannot paint.

## How it works

- `electron/transcript-cache.ts`: `TranscriptCacheStore` on `node:sqlite` (Electron 40 = Node 24; no native
  module). `putLatest` keeps the loaded prefix only when the new latest page overlaps it (by server row id), so a
  session that outgrew the window never stitches a gap. LRU 1000 sessions / 256 MiB, 8 MiB per entry (oldest
  rows drop first). File mode 0600.
- `electron/transcript-prefetch.ts`: pure selection/refresh logic with injected I/O.
- `electron/transcript-cache-host.ts`: IPC (`hermes:transcript-cache:*`; `read` is `sendSync`), name learning,
  prefetch wiring. `main.ts` injects `ensureRegistryBackend` (behind `backendDialClaims`) and
  `getJsonForBackend`; prefetch starts 15 s after launch.
- Renderer: `src/store/transcript-tail-cache.ts` keeps its old API over the bridge (in-memory twin without it);
  `use-session-actions` (`resumeSession`) adds the early REST read, the asleep branch and timing marks;
  `provisional-transcript.ts` gains `replace()`; `use-offline-fallback.ts` opens cached rows;
  `src/store/asleep-session.ts` and the guard at the top of `use-prompt-actions/submit.ts` refuse sends.
- Server: `hermes_state_messages.py` `_merged_lineage_page_from_conn` (SQL) and `_merged_lineage_page_python`
  (the old fold, now fallback and test oracle).

## Tests

- Cache write/read, merge, scoping, corruption: `electron/transcript-cache.test.ts` (and the renderer side,
  bridge and in-memory, `src/store/transcript-tail-cache.test.ts`).
- Passive remote dials and the sync read IPC: `electron/transcript-cache-host.test.ts`.
- LRU and size bounds: `electron/transcript-cache.test.ts` "LRU and size bounds" (count, bytes, per-entry trim).
- Prefetch selection and refresh on activity change: `electron/transcript-prefetch.test.ts` (per-gateway cap,
  chats before automations, refetch on `activity`/`message_count`, 30 s throttle, gateway backoff, 404).
- Open from cache then reconcile, no duplicates: `use-session-actions.test.tsx` "[daveey] instant opens…"
  (cache paint before any await; new rows once; unchanged session keeps the painted array; fresh page shown
  while the owner lookup is pending; one REST read).
- Asleep open: same describe (all cached rows, no getSession / gateway / REST / resume), sidebar click path in
  `offline-fallback.integration.test.tsx`, send refusal in `use-prompt-actions/index.test.tsx`.
- Timing log: `src/lib/open-timing.test.ts`.
- SQL paging equivalence: `tests/hermes_state/test_merged_lineage_sql_paging.py` (120 page shapes over a
  three-segment lineage with compaction generations, carried tails, model-only and tool rows; end-to-end
  `get_messages`; rewind reads keep the Python fold).

Measured 2026-10-27 on a test instance of this build (scratch userData, real gateways, local + mini + hub):
20 opens of the most recent sessions painted from cache in 16–48 ms; 18 of 20 reconciled within 2 s (median
~0.5 s). The two misses were mini sessions in non-default profiles (10.8 s, 9.6 s in the REST read itself: a
cold per-profile remote backend). With mini made unreachable, its chat opened read-only in 52 ms and a send
showed the asleep notice with the draft kept.

## Applying

Apply 0532, 0533 and 0548 with `git am -3` after the preceding fork patches. 0533 touches `use-session-actions`,
`submit.ts`, `main.ts` and `preload.ts`, so it depends on the fork's Desktop series (offline rows from 0528).

## Gotchas

- `read` is a synchronous IPC: it blocks the renderer while main is busy. Reads are a single indexed row.
- Rows written before a profile rename are re-keyed only for the local connection.
- An owner that is unknown at the click (pasted id, route restore) gets no early paint and no cache write.
- A row whose replica says offline but whose gateway is in fact up still opens read-only until the sidebar
  shows it live again; clicking the live row resumes normally.

## Follow-up patches (0623)

**0623** (`3285003cc2`): opens of another gateway's session spent 4.5-6.8 s in an "owner" stage. The owner probe (`probeStoredSession`) now answers at once from an awake hub-replica row that matches the named owner (`fallbackSessionRow`); a resume that names no owner takes it from the awake replica row so the cached transcript paints before any await (asleep rows keep the read-only path); the prefetcher puts sessions behind open Inbox cards first, ahead of the per-gateway 200-slot people-first ordering. Code: `electron/transcript-cache-host.ts`, `transcript-prefetch.ts`, `src/app/session/hooks/use-session-actions/{index,utils}.ts`. Tests: `transcript-cache-host.test.ts`, `transcript-prefetch.test.ts`, `use-session-actions.test.tsx`, `resolve-stored-session.test.ts`.
