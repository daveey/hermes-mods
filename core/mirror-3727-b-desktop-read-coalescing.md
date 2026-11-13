# Desktop shares identical in-flight backend reads

Patches: **3721**.

## Behavior
- When several callers in the Desktop app issue the same bodyless GET to a backend at the same moment, only one request goes over the wire; the others wait for its answer.
- Each caller receives its own copy of the result, so one caller mutating its rows does not affect another.
- A write to a backend invalidates sharing: a read issued after a write never joins a read sent before it.
- A failed shared read fails every waiting caller once; the next read asks the backend again.
- Reads are keyed on backend origin, credential, headers, gateway mode, path, deadline, pool and profile scope; any difference keeps reads separate.

## How it works
3721 adds `read-coalescing.ts` and routes `fetchJsonForBackend` (the single sink for renderer-routed and main-process backend REST calls) through it. The previous transport body is renamed `fetchJsonForBackendOnce`. Settled reads are not cached; only in-flight ones are shared. Each backend has a generation counter bumped by any write. The motivation was bursts of duplicate session-list reads queuing on a slow tunnel and causing "Timed out connecting to Hermes backend" errors.

## Code
- `apps/desktop/electron/read-coalescing.ts`
- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/local-gateway.test.ts`
- `apps/desktop/electron/remote-down-gate.test.ts`
- `apps/desktop/electron/slow-link.test.ts`
- `apps/desktop/electron/read-coalescing.test.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/electron/read-coalescing.test.ts`: `sends one request for identical concurrent reads and gives each caller its own copy`, `asks again once the shared read has settled`, `keeps reads apart when anything on the wire or the deadline differs`, `never coalesces writes, and a read after a write does not join a read sent before it`, `shares a failure with every caller and then lets the next read try again`, `treats only bodyless GETs as reads`, `sends every backend REST call through the coalescer, and the coalescer only to the real transport`.
- The existing wiring tests in `local-gateway.test.ts`, `remote-down-gate.test.ts` and `slow-link.test.ts` are updated to slice the renamed transport function.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Only bodyless GETs coalesce; the wiring test expects no other caller of the uncoalesced transport.
- The real-world reduction in timeouts is not covered by any listed test.
