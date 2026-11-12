# Desktop sidebar: one batched primary read for a concrete local profile

Patches: **2574**.

## Behavior

- With registry gateways or remote profiles configured, the Desktop's intercept of `GET /api/profiles/sessions/sidebar` used to always fan out to three `/api/profiles/sessions` reads. For a concrete profile with no remote override it now makes one read of the primary's batched `/api/profiles/sessions/sidebar` route with the original query.
- Unowned rows in all three slices (`recents`, `cron`, `messaging`) get the primary owner tag as `connection_id`; the response is otherwise returned unchanged (copy, no mutation; a null tag returns it as is).
- Any failure of the batched read (older primary answering 404, timeout, transport error) falls back to the three-slice path for that request only; nothing is remembered, so the next refresh tries the batched read again.
- The `all` profile scope and remote-override profiles keep the three-slice path.

## How it works

- `profile-session-routing.ts` gains `batchedPrimarySidebarProfile` (reads `recents_profile`, default `all`), `tagSidebarSlicesOwner`, `fanOutSidebarSessionSlices` and `fetchSidebarSessionsBatchedOrSliced`.
- In `main.ts`, `interceptSessionRequestForRemote` calls `fetchSidebarSessionsBatchedOrSliced` after the existing remote/registry and pinned-source guards.

## Code

`apps/desktop/electron/main.ts`, `apps/desktop/electron/profile-session-routing.ts`, `apps/desktop/electron/profile-session-sidebar-batch.test.ts` (new).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `apps/desktop/electron/profile-session-sidebar-batch.test.ts` (vitest). Entry path: the exported routing functions with a recording fake `fetchJsonForProfile`. Covered: one batched request, key preservation and owner tagging without mutation, null tag, fallback on 404 and timeout with the slice result returned, retry on every refresh, `all` and override scopes, and `recents_profile` scope reading. The `main.ts` intercept wiring and a real primary gateway are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- A failed batched read costs an extra request before the fallback, on every refresh while the primary lacks the route.
- Performance numbers in the commit message (three list reads reduced to one) are measurements from the author's setup, not checked here.

## Plugin

Core change; no plugin required.
