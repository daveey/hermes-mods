# Desktop WebSocket dial limits follow slow-link mode

Patches: 3814.

## Behavior

Desktop chat WebSocket connects use the normal fifteen-second limit, stretched to at most sixty seconds for a known SSH-forwarded backend in slow-link mode. Local, cloud and unknown origins retain the normal limit.

## How it works

Electron main tracks remote origins and their scope/alive callback, exposes a synchronous per-URL limit over preload IPC, and accepts outcome reports. The renderer's `dialWithLinkLimit` obtains that limit immediately before calling the shared JSON-RPC client's per-dial timeout override. A typed timeout error identifies only connect-limit failures.

An open records an answer sample and clears timeout strikes through the existing tracker; a timeout counts a strike only when the SSH master is still alive. Dropped scopes lose their remote mapping. Invalid/missing bridge values fall back to the shared client's default, and advisory reporting cannot change the dial result. Backend retirement remains with the dispatch probe rather than this reporting helper.

## Code

- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/preload.ts`
- `apps/desktop/electron/slow-link.ts`
- `apps/desktop/src/api/client.ts`
- `apps/desktop/src/api/gateway-link-dial.ts`
- `apps/desktop/src/global.d.ts`
- `apps/shared/src/index.ts`
- `apps/shared/src/json-rpc-gateway.ts`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `apps/desktop/src/api/gateway-link-dial-slow-link.test.ts` (patch 3814).
- `apps/desktop/src/api/gateway-link-dial.test.ts` (patch 3814).

- **3814:** The slow-link integration file drives the real tracker and shared client with fake sockets/timers: normal/local 15s versus remote slow 60s, 20s successful open, live/dead SSH strikes, answer reset, malformed inputs and IPC/drop-scope behavior. The dial file checks HermesGateway forwarding, typed timeout reporting, non-timeout preservation and absent bridge defaults.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

An alive SSH master does not prove the backend answered. Slow mode is capped, not an unlimited connect wait. Non-timeout failures are not strikes, and a dead tunnel must not be labelled merely slow. These are client/unit integration tests, not a fresh real-network performance measurement.

## Plugin?

No. Shared client timeout typing and Electron main/preload/renderer IPC need core application changes.
