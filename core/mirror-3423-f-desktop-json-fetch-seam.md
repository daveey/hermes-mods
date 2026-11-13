# Desktop JSON fetch seam and probe pool tests

Patches: **3256, 3285**.

## Behavior
- The Desktop app's `fetchJson` / `fetchPublicJson` helpers live in `electron/json-fetch.ts` (`fetchJsonWith` / `fetchPublicJsonWith`), so tests drive them directly instead of only proving `sendWithRequestDeadline`.
- Covered behavior: the `probe` pool is small and queues requests beyond its socket limit while the default JSON pool is not held to it; a large body gets a body-scaled deadline budget while a small body gets only a few ms extra; a POST dropped after the server read it is not replayed (body-sent gate) while a dropped GET is retried.
- 3285 exports `PROBE_POOL_MAX_SOCKETS` and derives test sizes from it; the loopback test servers share one `loopbackServer(handler)` helper. No user-visible change.

## How it works
- 3256 moves the helpers out of `main.ts` with injected remote-header/config seams. 3285 exports the constant and refactors tests.

## Code
- `apps/desktop/electron/json-fetch.ts`
- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/api-transport.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/electron/json-fetch.test.ts` (vitest): "pool: 'probe' rides the small probe pool, so a third concurrent probe queues"; "the default JSON pool is not held to the probe limit"; "gives a large body the body-scaled budget: a POST outlasting the base deadline still answers"; "a small body gets only its own few ms on top: the same busy server times it out at that budget"; "marks the body sent: a POST dropped after the server read it is not replayed"; "a dropped GET is still retried (idempotent), so the drop above is the bodySent gate, not a de..."; and, from 3285, "pool: 'probe' rides the small probe pool, so one probe past its socket limit queues".
- `apps/desktop/electron/remote-down-gate.test.ts`: adjusted by 3256.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- None recorded.
