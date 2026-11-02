# Desktop gateway restart failure logs

Patch **1674** (`3e1b13e59ae8ea766efa0ba637a0324ae5b228ca`).

## Behavior

A failed stale-owner restart records the last 20 nonblank stdout lines and last 20 nonblank stderr lines as separate sections, explicitly naming empty streams. Success remains a single line. Desktop still attempts a given stale-owner restart only once; the failure log states that Desktop does not retry and that hermes-sync owes the next restart.

## How it works

`formatGatewayRestartLog()` formats the exit code and stream tails. `restartLocalGatewayOwner()` passes its bounded CLI result to this formatter instead of retaining only the last combined line. `createStaleGatewayRestarter()` is unchanged: subsequent dials for the same stale owner attach as-is rather than issuing repeated restarts.

## Code

`apps/desktop/electron/local-gateway.ts` (formatter and tail helper), `apps/desktop/electron/main.ts` (logging call site).

## Tests

`apps/desktop/electron/local-gateway-restart-failure-log.test.ts` adds:

- `a failed restart keeps the last 20 stdout and last 20 stderr lines separately`
- `an empty stream on failure is named, not dropped`
- `a successful restart stays one line`
- `a failing owner restart is attempted once and logged with both streams`

The last case composes the real bounded client and restarter with an executable failing fake CLI; it skips Windows. These source tests were not rerun in this mirror. They do not exercise the packaged Electron app or prove the cause of a historical restart failure.

## Applying

Replay 1674 with the [ordered mirror series](README.md) and rebuild Desktop using the source repository's supported workflow. Gateway handback ordering is separately documented under [restart/drain](gateway-restart-drain-holds-and-reattach.md).

## Gotchas

This is diagnostic retention, not a retry policy or an automatic guarantee that a later restart succeeds. Earlier lines beyond each stream's tail are intentionally discarded; whitespace-only lines do not consume the tail budget. No plugin/configuration is added.
