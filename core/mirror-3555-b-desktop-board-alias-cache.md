# Desktop Kanban current-board alias cached for 30 s

Patches: **3541**.

## Behavior
- The Desktop Kanban plugin resolves the server-current board alias (empty board slug) once per 30 s per routed scope instead of issuing a full boards read before every board read. Concurrent refetches share one read.
- If the boards read fails, the last known board slug is kept, so remote gateways keep showing the same board.

## How it works
- 3541 routes `resolvedSlug()` through `queryClient.fetchQuery` under the key `['kanban', 'current-board', <routed scope>]` with a 30 s stale time and no retry. Board refetches fire on every events-socket frame, poll and query retry, which previously each caused a boards read over a slow remote link.

## Code
- `apps/desktop/src/plugins/kanban/api.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/src/plugins/kanban/api.gateways.test.ts`: `reads the routed gateway's /boards once for the '' alias, not before every board read`, `keeps the last known current board when the routed /boards read fails, and retries it next time`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Board payload volume on saturated links is not addressed here.
