# Desktop Kanban events: never resume at since=0, coalesce re-reads

Patches: **3581**.

## Behavior
- When the merged board snapshot has no known event cursor (for example the routed gateway did not answer, which merges as `latest_event_id: 0`), the events socket opens at the server tail rather than at `since=0`, so it no longer replays the whole event history.
- Event frames refresh the board, boards and blocked queries at most once per 2 s per connection (one leading and one trailing refresh); per-task detail invalidation stays immediate.

## How it works
3581 adds `knownCursor()`, which accepts only positive cursors, used by `snapshotCursor()` and the post-fetch tail, and documents 0 as "unknown" in `mergeBoards`. Frame-driven invalidation is throttled with a leading and a trailing call and `cancelRefetch: false`, so an in-flight read is reused instead of orphaned.

## Code
- `apps/desktop/src/plugins/kanban/api.ts`
- `apps/desktop/src/plugins/kanban/api.events-cursor.test.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/src/plugins/kanban/api.events-cursor.test.ts`: `never opens at since=0 from a snapshot whose routed gateway did not answer`, `opens at the server tail when the fetched snapshot carries no positive cursor`, `coalesces a burst of frames into one immediate and one trailing board re-read`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
Socket reconnects reuse the dial-time `since`, so they can still replay events since the first dial; the coalescing only bounds the cost (recorded as deferred, untested).
