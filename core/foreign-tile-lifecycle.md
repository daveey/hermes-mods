# Preserve owner-routed foreign Desktop tiles

Patch: 0291.

## Behavior

An ambient connection refresh no longer strips a foreign owner's runtime from a correctly owner-routed tile. Owner-side missing sessions remain retryable rather than being classified as a permanent wrong-backend tile error. Switching connection IDs still hides and restores runtime-less persisted workspace tiles; this patch does not remove workspace isolation.

## How it works

Removes the ambient identity guard from the controller and tile module, and removes wrong-backend classification from resume recovery. Conservative restored-dead-tile deletion checks and owner-scoped routing remain intact.

## Code

`apps/desktop/src/app/chat/session-tile.tsx`, `session-tile.test.ts`, `session-tile-owner-lifecycle.test.tsx`, and `apps/desktop/src/app/contrib/controller.tsx`.

## Tests

The implementation commit reports 255 targeted tests across 10 files plus ESLint and TypeScript checks. The new lifecycle integration imports the real controller/open/resolver/store/delegate and mounts SessionTilePane with transport and ChatView mocked: four failures and one pass before the fix, five passes after. This mirror does not independently repeat the Desktop suite or establish live Electron deployment.

## Applying

Apply the full ordered patch series through 0291 at its merge-base; rebuild Desktop separately.

## Gotchas

Changing workspace connection IDs intentionally hides tiles; same-scope base URL or token refresh must not discard an existing foreign runtime. A mocked renderer integration is not a live cross-gateway smoke test. No hotkeys change.

## Could this be a plugin instead?

No: the affected controller subscription and tile recovery are core-owned.
