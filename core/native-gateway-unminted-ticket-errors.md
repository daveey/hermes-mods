# Desktop: unminted native gateway descriptors surface the ticket mint error

Patch **2032** (`e71ada4e5910b0a38e09066fe12458ea4678765d`, `[daveey] Preserve unminted native gateway ticket errors`). Related: [desktop-native-gateway-ticket-recovery.md](desktop-native-gateway-ticket-recovery.md) (1719).

## Behavior

- A local Electron connection whose descriptor URL carries `native_dial=unminted` has no credential of its own. When the IPC ticket mint for it rejects (`control socket busy`) or refuses (`{ ok: false, error: 'gateway restarting' }`), `resolveGatewayWsUrl` now throws that original error instead of returning the descriptor URL.
- The caller (for example `openGatewayForAgent`) therefore sees the real reason, and no socket is opened.
- Retained secondary connections still recover: the reconnect loop retries the mint and dials the fresh ticket URL once the mint succeeds.
- Legacy native descriptors (`native_dial=1`) keep the earlier fallback: a failed mint returns the cached URL, and the socket factory then refuses it with `Native gateway requires a fresh private ticket`.

## How it works

`apps/shared/src/websocket-url.ts` computes `mintRequired` as `native_dial === 'browser'` (the browser-hosted Desktop, already mint-required) **or** `native_dial === 'unminted'`. When `mintRequired`, a rejected mint rethrows and a refused mint throws `fresh.error`; otherwise the function falls through to `conn.wsUrl` as before.

## Code

- `apps/shared/src/websocket-url.ts`: `resolveGatewayWsUrl`.

## Tests

- `apps/desktop/src/lib/gateway-ws-url.test.ts`, `local Electron native dial`: `surfaces the original error when the mint rejects`, `surfaces the original error when the mint refuses`, `dials the freshly minted URL`.
- `apps/desktop/src/store/gateway-local-ticket-reconnect.test.ts`: new `surfaces an unminted IPC refusal to the registry caller before opening a socket`; the retained-secondary recovery table now also runs local `backoff`/`request` cases with an unminted descriptor (factory-refusal error asserted only for the legacy descriptor).

```sh
cd apps/desktop && npx vitest run src/lib/gateway-ws-url.test.ts src/store/gateway-local-ticket-reconnect.test.ts
```

Not rerun in this mirror pass; listed from the patch.

## Applying

Desktop/shared TypeScript only. Apply through the snapshot-bound replay in [README.md](README.md); it builds on the 1719 ticket-recovery code.

## Gotchas

- Only `native_dial=unminted` and `native_dial=browser` are mint-required. Remote descriptors and `native_dial=1` keep the cached-URL fallback.
- Callers that previously relied on a resolved URL now need to handle a rejection; the registry reconnect path already does.

## Plugin?

No. Gateway URL resolution runs in the Desktop shell before any plugin loads.
