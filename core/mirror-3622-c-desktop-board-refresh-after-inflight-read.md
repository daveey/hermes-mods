# Desktop kanban events: a board refresh that finds a read in flight re-reads after it settles

Patches: **3610**.

## Behavior
- When the Desktop kanban plugin's trailing board refresh fires while the leading board read is still in flight (for example over a slow tunnel), it no longer gets absorbed by that read. Once the in-flight read settles, the board is read once more, so events newer than the first read's snapshot appear without waiting for the next event frame or the board poll (15 s across gateways, 60 s otherwise).
- The in-flight read is never cancelled and never joined.
- A steady event stream over a slow tunnel still re-reads at most about once per `EVENTS_BOARD_REFRESH_MIN_MS` (2000 ms), one read at a time.

## How it works
- 3610 adds `invalidateAfterInFlight(scope, queryKey)` in `plugins/kanban/api.ts`. If nothing is fetching on the key it invalidates at once; otherwise it registers one query-cache subscription per key (tracked in `boardRefreshWaits`) and, when the fetch settles, restarts the coalescing interval for the scope and invalidates again. `refreshBoardReads` uses it for the board-wide prefix, the boards list key and the blocked key. `resetBoardRefresh` drops pending waits. The cause recorded: `cancelRefetch: false` joined the in-flight promise whose success cleared `isInvalidated`.

## Code
- `apps/desktop/src/plugins/kanban/api.ts`
- `apps/desktop/src/plugins/kanban/api.events-cursor.test.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/src/plugins/kanban/api.events-cursor.test.ts`: `re-reads the board once after a read that a trailing refresh found still in flight` (the first read is not aborted, exactly one more read follows, no further reads after it).
- Untested: the boards-list and blocked-list keys specifically, and `resetBoardRefresh` dropping waits.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A refresh requested while a wait is pending is considered covered by it; only one wait exists per query key.
