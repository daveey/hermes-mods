# Desktop: @session refs and tiles paint from the transcript cache

Patches: **1889** (`085866cb50`, `[daveey] desktop: warm transcript cache for @session-referenced sessions so first ref clicks paint from cache`), **1890** (`895d0d1549`, `[daveey] desktop: tiles paint from the transcript cache while REST is in flight`), **1891** (`c76f0f3c9f`, `[daveey] desktop: review fixes for the tile cache paint`), **1893** (`73154c72c8`, `[daveey] refactor(desktop): move useOverviewPreviews out of sidebar/index.tsx`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- @session ref chips and links ask main to prefetch the transcript key their click would paint from, on render and pointer-enter (1889).
- A tile opened by a ref click paints cached rows while the latest-page REST read is in flight, then reconciles to the REST page (1890); explicit refreshes still wait on REST and late rows merge (1891).
- `useOverviewPreviews` moves out of `sidebar/index.tsx` to keep it under its line cap after the upstream sync (1893).

## How it works

`apps/desktop/electron/transcript-prefetch.ts` and `transcript-cache-host.ts` warm the cache; `apps/desktop/src/app/contrib/hooks/tile-cache-paint.ts` paints tiles.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1889: An @session mention usually names an old session outside the prefetcher's PER_GATEWAY window, so its first click waited on hub REST (paint:"rest", 1.5 s on 11-02). Ref chips/links now ask main to prefetch the key the click would paint from (owner resolved like the click: resolveSessionRefOwner), on render and on pointer-enter, via a fire-and-forget hermes:transcript-cache:prefetch IPC. Main dedupes queued/in-flight keys, skips a current cache (hub meta from the last pass, else MIN_REFETCH_MS), shares the tick's per-gateway backoff, and runs at most 2 per gateway.
- 1890: An @session ref click opens a tile, and resumeTile always waited on the latest-page REST read, so the warmed cache never reached the screen. When REST has not landed by the time session.resume binds, the tile now publishes the cached rows (same owner scope as the REST read) and reconciles to the REST page when it lands, refreshing the cache. Tile opens write an [open-timing] line tagged surface:tile, so paint:cache is observable.
- 1891: An explicit refresh (refreshTranscript) never paints the cache, so callers that await fresh rows still get the REST page. Tests: rows landing after the cache paint are merged (mutation-checked: an always-replace publish fails), REST failure after a cache paint keeps the rows and logs outcome failed, refreshTranscript waits on REST, queue-full keys resolve dropped. Fix the prefetchKey doc (unlisted copies use UNLISTED_REFETCH_MS).
- 1893: Keeps sidebar/index.tsx under its FILE_LINES cap on main (2038) after the upstream sync.

## Code

`apps/desktop/electron/preload.ts`, `apps/desktop/electron/transcript-cache-host.ts`, `apps/desktop/electron/transcript-prefetch.ts`, `apps/desktop/src/app/chat/session-ref-prefetch.ts`, `apps/desktop/src/components/assistant-ui/directive-text.tsx`, `apps/desktop/src/global.d.ts`, `apps/desktop/src/app/contrib/hooks/tile-cache-paint.ts`, `apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.ts`, `apps/desktop/src/lib/open-timing.ts`, `apps/desktop/src/app/chat/sidebar/index.tsx`, `apps/desktop/src/app/chat/sidebar/use-overview-previews.ts`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `apps/desktop/electron/transcript-cache-host.test.ts`: `[daveey] prefetch IPC stores an @session ref's latest rows under its key and skips the fetch when current`, `reads the replica Inbox on the primary and fetches a card's session first`
- `apps/desktop/electron/transcript-prefetch.test.ts`: `fetches a session outside the PER_GATEWAY window into the cache, once per MIN_REFETCH_MS`, `dedupes a key already queued or in flight`, `uses the hub meta from the last pass: unchanged is current, moved is refetched`, `backs off a gateway that failed (shared with the tick) and runs at most DEMAND_CONCURRENCY per gateway`, `does not refetch a missing session within MIN_REFETCH_MS, and rejects a key without an owner`, `fetches a session outside the hub listing into the cache, then only once the copy is UNLISTED_REFETCH_MS old`, `a store that throws (closed at quit) resolves failed instead of throwing out of the IPC listener`, `drops keys past DEMAND_QUEUE_MAX queued on one gateway`
- `apps/desktop/src/app/chat/session-ref-prefetch.test.tsx`: `render + pointer-enter issues exactly one prefetch for the resolved owner key`, `a second render within MIN_REFETCH_MS issues none; after the window it sends again`, `an unresolved owner issues none, then a hover after the owner is known sends it`, `a gateway-qualified ref targets that gateway (markdown link)`, `an unknown gateway qualifier issues none`, `the warmed key is exactly the scope the open reads its cache paint from`
- `apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.cache-paint.test.ts`: `binds with the cached rows while REST is in flight, then reconciles to the REST page`, `without a cached copy it waits on REST exactly as before and logs paint "rest"`, `a cached copy under another owner key is not painted`, `rows that landed after the cache paint are merged with the REST page, not overwritten`, `a REST failure after the cache paint keeps the cached rows and logs outcome "failed"`, `an explicit refresh (refreshTranscript) still waits on REST and never paints the cache`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
