# Desktop read sharing isolates native grants and invalidates on WS writes

Mirrored entries: 3890.

## Behavior

Concurrent identical backend reads share only within the same native grant identity, not across profiles/instances/authority epochs behind one API origin. Reads after a write settles never join an older read sent while that write was still in flight.

Renderer WS-RPC mutations announce write boundaries to Electron so REST read sharing cannot return a stale pre-mutation list. Reads and nonmutation controls do not send those notices.

## How it works

The coalescing key replaces a boolean native-endpoint marker with the grant tuple (profile_id, instance_id, authority_epoch), using null when absent. REST writes advance the origin generation at start and settlement. `ReadCoalescer.noteWriteEverywhere` adds a global generation for writes whose backend is not identified by the renderer notice.

`isBackendWriteMethod` classifies mutations, policy edits and session.create. `HermesGateway.request` sends the optional preload note before the wire operation and again in finally, including refusals. Preload forwards it through a dedicated IPC channel to the shared main-process coalescer. `profileNamesOf` extracts valid nonempty names for the all-profiles routing path and reduces main-module growth.

## Code

- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/preload.ts`
- `apps/desktop/electron/profile-session-routing.ts`
- `apps/desktop/electron/read-coalescing.ts`
- `apps/desktop/src/api/canonical-protocol.ts`
- `apps/desktop/src/api/client.ts`
- `apps/desktop/src/global.d.ts`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `apps/desktop/electron/read-coalescing.test.ts`: Asserts different native profile/instance/epoch or missing grant do not share while identical copied grants do; a post-settlement read does not join an in-flight older read; global write notices invalidate generations while subsequent fresh reads still share. Source-wiring assertions connect preload IPC to the main coalescer.
- `apps/desktop/src/api/backend-write-notice.test.ts`: Classifies named write/read/control methods, uses a real local WebSocket fixture to assert session title/archive notices bracket the canonical mutation wire call, and requires the after-notice even for a refused mutation.
- `apps/desktop/electron/profile-session-routing.test.ts`: Pins profileNamesOf ordering and filtering of empty, missing, nonstring or malformed profile listings.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

Coalescing shares in-flight reads, not a persistent result cache. A WS notice deliberately invalidates all backend generations because it lacks a scoped backend identity, trading some sharing for correctness. Optional preload absence is a no-op. Fixture WebSocket traffic and source-wiring checks are not a launched Desktop integration test.

## Plugin?

No. Grant isolation and renderer-to-main invalidation must protect the core transport coalescer.
