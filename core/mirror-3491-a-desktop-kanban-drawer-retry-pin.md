# Desktop kanban drawer: pin refetch-restart reads as polls

Patches: **3475–3476**.

## Behavior
- No runtime change. The Desktop kanban drawer classifies a read as a foreground "open" read or a poll; a fetch that replaces a failed one during its retry delay (cancelled by a socket invalidation with `cancelRefetch`) is a poll, not a retry of the user's read. This is now pinned by a test.
- A fetch cancelled in a failed read's retry delay also settles that kind's open read, by design: the user's read already went out foreground and failed.

## How it works
- 3475 adds a drawer test: warm cache, re-open with the task read failing (retry 1), wait for one failure, then invalidate; the expected read kinds are foreground, foreground, then two polls. It also expands the `retryingFetch` comment in `api.ts` to name the query-core reducer details it relies on, so a React Query upgrade knows what to recheck. The behavior depends on `undefined !== null` in query-core's restart condition.
- 3476 documents the by-design settle in `userOpenReads` and raises the test's retry delay from 300 ms to 10 s so a loaded runner cannot let the retry beat the invalidation.

## Code
- `apps/desktop/src/plugins/kanban/api.ts`
- `apps/desktop/src/plugins/kanban/drawer.test.tsx`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/src/plugins/kanban/drawer.test.tsx`: `makes the reads of a fetch that replaced a failed one during its retry delay polls`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The pinned behavior rests on a query-core internal (`fetchMeta` null vs undefined) of the installed version; a library bump may break the test.
