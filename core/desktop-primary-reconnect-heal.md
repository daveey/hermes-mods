# Desktop: bounded primary reconnect refresh, healed owner-pinned requests

Patches: **1874** (`bea3dcc5bb`, `[daveey] fix(desktop): bound primary reconnect refresh and heal owner-pinned requests on a closed primary`), **1875** (`9ba5754ca9`, `[daveey] fix(desktop): keep store/gateway.ts under its line cap by moving the primary wait into the recovery module`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- After a primary gateway reconnect, the refresh is bounded and requests pinned to a closed primary are healed onto the new connection instead of hanging (1874); the wait moves into the recovery module (1875).

## How it works

`apps/desktop/src/store/primary-gateway-recovery.ts` owns the wait; `use-gateway-boot.ts` drives the redial.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1874: [daveey] fix(desktop): bound primary reconnect refresh and heal owner-pinned requests on a closed primary
- 1875: [daveey] fix(desktop): keep store/gateway.ts under its line cap by moving the primary wait into the recovery module

## Code

`apps/desktop/src/app/gateway/hooks/use-gateway-boot.ts`, `apps/desktop/src/store/gateway.ts`, `apps/desktop/src/store/primary-gateway-recovery.ts`, `apps/desktop/src/test/gateway-boot-harness.tsx`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `apps/desktop/src/app/gateway/hooks/use-gateway-boot-primary-redial.test.tsx`: `redials a local primary after a close, and again after a failed dial (error)`, `redials a drop that lands while the post-reconnect refresh hangs`, `arms the backoff when the socket dropped while boot was still refreshing`, `arms the backoff when the socket dropped while a connection apply was finishing`, `sends the next local request on the redialed primary while a remote is foregrounded`, `coalesces concurrent requests onto one primary dial`, `never sends a request aborted while it waited for the redial`, `keeps the caller deadline and does not send after it expired`, `does not replay a request that was already sent when the socket dropped`, `does not dial from a request once the boot hook is gone`, `does not race a connection apply with its own dial`
- `apps/desktop/src/store/primary-gateway-recovery.test.ts`: `cleans up a synchronously delivered open subscription`, `does not invoke the new owner for an old gateway`, `rejects old waiters and removes subscriptions when ownership is replaced`, `charges recovery time to the caller deadline`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
