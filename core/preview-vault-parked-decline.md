# Desktop preview: a parked bystander window declines vault requests

Patch **2041** (`dee02b0403f6145ddc4a5d17a8bc9d7bd64971a1`, `fix(preview): decline vault requests from parked bystander windows [daveey]`). Related: [hidden-preview-open.md](hidden-preview-open.md), [plugin-open-preview.md](plugin-open-preview.md).

## Behavior

- A hidden session's Browser page stays parked in the window that last showed it, and that window keeps answering the session's background `preview.read`/`preview.act` requests so the agent can keep working.
- `preview.act` with `action: 'vault'` (for example `vault.operation: 'open'`) is now excluded from that claim. The parked window **declines** it, the way any non-claiming window declines a window-owned request, and does not run the handler.
- The backend counts a decline as that client's vote and keeps the request open, so the visible owner window answers the vault request. Previously the parked window ran the vault handler with `isActiveSession: false`, and its inactive-session refusal could settle the request first (the backend keeps the first response).

## How it works

In `handleServerRequest` (`server-requests.ts`), on the `route === 'ignore'` branch for `WINDOW_OWNED_REQUESTS`, the background-preview fast path now also requires `request.params.action !== 'vault'`. Vault requests fall through to `declineNotShown(request)`.

## Code

- `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/server-requests.ts`: `handleServerRequest`.

## Tests

- `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/server-requests.test.ts`, suite `a hidden session keeps driving its own parked Browser page`: new `declines vault open in a parked window so the visible owner can answer` (decline called once, respond not called). Existing cases in the suite cover non-vault parked `preview.act`.

```sh
cd apps/desktop && npx vitest run src/app/session/hooks/use-message-stream/gateway-event/server-requests.test.ts
```

Not rerun in this mirror pass; listed from the patch.

## Applying

Desktop-only. Apply through the snapshot-bound replay in [README.md](README.md); it depends on the background-preview claim (`BACKGROUND_PREVIEW_REQUESTS`, `windowHoldsBackgroundPreview`) from earlier patches.

## Gotchas

- Only the parked-window branch changed. Whether the visible owner can satisfy the vault request is unchanged, and the test does not exercise the owner's side.
- If no attached window owns the session, every window declines and the backend settles the request as declined.

## Plugin?

No. Server-request routing between Desktop windows is part of the core message stream.
