# Desktop Kanban drawer: foreground reads on a slow link

Patches: **3377, 3393, 3423**.

## Behavior
- On a slow link the Desktop thins background polls; a user-driven read of the Kanban task drawer is now tagged `priority: 'foreground'` so it is not thinned. The first task and log read of each open are foreground; interval and socket refetches of that open remain background polls.
- A retried failed first read stays foreground until one read of that kind has answered.
- Re-opening a task whose cached detail/log is still fresh now sends its read at mount (`refetchOnMount: 'always'`), at the cost of one task and one log GET per open.
- A kind whose reads all fail stops sending foreground refetches once a fetch has spent React Query's retries, so a dead gateway or missing task is not polled at foreground priority forever.
- The slow-link tracker logs its "is slow" entry line even when a probe times out before any remote request has answered, and its documentation now names the cost that a hung remote behind a live tunnel is retired only after three probe timeouts.

## How it works
- 3377 adds `priority: 'foreground'` to plugin REST options (forwarded by `pluginRest`), has `dispatchSlowLink` take the log and wire it into the tracker, and tags the drawer's first reads via `userOpenReads`.
- 3393 wraps the read in `userOpenReads` so the tag is kept until one read answers, and sets `refetchOnMount: 'always'` on both drawer queries.
- 3423 passes `retryingFetch(context)` so React Query's `fetchFailureCount` marks a kind settled when a fetch has spent its retries, and corrects interval comments (log 3–15 s, task 30 s).

## Code
- `apps/desktop/electron/slow-link.ts`
- `apps/desktop/electron/main.ts`
- `apps/desktop/src/api/plugins.ts`
- `apps/desktop/src/plugins/kanban/api.ts`
- `apps/desktop/src/plugins/kanban/drawer.tsx`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/electron/slow-link.test.ts`: `dispatchSlowLink logs the slow-link entry line when a probe times out before any remote has answered`.
- `apps/desktop/src/plugins/kanban/drawer.test.tsx`: `tags the reads of each open foreground, so a slow link never skips a re-open; refetches stay polls`, `keeps a retried first read foreground: a read counts as delivered only once it answered`, `sends a foreground read when a task is re-opened while its cache is still fresh`, `makes the refetches of a kind whose reads all fail polls once its first fetch spent its retries`.
- `apps/desktop/src/api/plugins.test.ts` was touched by 3377; its test names were not separately read.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Re-opening a recently opened task costs one extra task GET and one log GET.
- The tag is per open: a re-open of a task whose cached query is in error is still a foreground read.
