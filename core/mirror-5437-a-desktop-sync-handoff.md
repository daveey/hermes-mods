# Desktop Update hands off to hermes-sync on managed hosts

Mirror entries: 5435, 5436, 5437.

## Behavior

- Desktop's checkout-based client Update and backend Update request the existing hermes-sync force-update path when the target host reports that hermes-sync manages its core updates. They do not also start Desktop's own updater.
- A managed host with nothing pending shows an informational handoff state rather than falling back to its own update. A failed force request shows an error unless a follow-up state read establishes that nothing remains pending.
- Backend Update dismisses the update notification and shows the preparing stage before querying the handoff route. The terminal `synced` dialog explains the handoff; Done closes it and resets the apply state.
- Packaged Desktop bundle updates keep their existing mechanism. Unmanaged hosts, older backends without the management flag, and unreadable force-state endpoints retain the previous update route.
- The backend reports `managed` on `GET /api/hermes/update/force` and refuses the ordinary `POST /api/hermes/update` on managed hosts as a backstop for older clients.
- The handoff title and explanation are registered in the Desktop key catalog and known translation gaps, retaining English fallback rather than adding translations.

## How it works

`_hermes_sync_manages_host()` uses a fresh deployment-window record returned by `read_window()`, not the mere presence of a sync installation. An unreadable record is treated as unmanaged. Force-state responses distinguish management from availability: hermes-sync can manage a host without currently owing an update.

`routeUpdateToHermesSync()` checks management before requesting the existing force endpoint. Client handoff is limited to `posix-handoff` and explicitly targets the local connection; backend handoff follows the active backend scope. The explicitly scoped state probe is passive so that asking about the local host does not cold-start a backend in remote mode.

A managed response never falls through to the old updater. On a request error, another state read handles a race in which the pending update has already landed; otherwise the apply store enters an error state. Backend update calls share an in-flight promise, cleared after completion. Notification dismissal and the preparing state precede routing, including when routing selects hermes-sync.

The `synced` stage is terminal and closeable, not evidence that installation has completed. Its copy describes the existing sync protocol's intended pause-at-step-boundary and continuation behavior. These entries route to that protocol; they do not implement it. The overlay uses a message-only view and includes `synced` among the states reset on close.

## Code

Paths refer to the mirrored core source tree.

- `hermes_cli/web_routers/actions.py`
- `apps/desktop/src/api/system.ts`
- `apps/desktop/src/store/updates.ts`
- `apps/desktop/src/app/updates-overlay.tsx`
- `apps/desktop/src/global.d.ts`
- `apps/desktop/src/types/hermes.ts`
- `apps/desktop/src/i18n/en_force_update.ts`
- `apps/desktop/src/i18n/types_force_update.ts`
- `apps/desktop/src/i18n/overlay-gaps.json`
- `locales/_keys.desktop.json`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Desktop store tests mock API and bridge calls to cover local-client and active-backend routing, avoiding the old updater on managed hosts, managed-but-unavailable responses, request failure and the subsequent no-pending race. They also cover packaged-client bypass, unmanaged and unreadable-state fallback, repeated backend calls after completion, notification dismissal and preparing state during the state query.

The overlay rendering test supplies a `synced` state, checks its title and explanation, and clicks Done to verify closure and reset. Backend tests cover managed availability and force-request reporting through the route functions and authenticated HTTP force endpoints. Added cases cover managed-but-idle messaging, a stale window becoming unmanaged, and ordinary-update refusal by direct route invocation.

Source test paths:

- `apps/desktop/src/store/updates-sync-route.test.ts`
- `apps/desktop/src/app/updates-overlay.synced.test.tsx`
- `tests/hermes_cli/test_core_update_force.py`

Real transport scope/passive behavior is untested by the new mocked store tests. The ordinary-update refusal's HTTP entry path is untested by the added refusal case. Live sync installation, busy-chat pause/continuation, native Desktop updater execution and the manual terminal `hermes update` fallback are untested here. Entry 5437 adds no tests; translation-catalog validation and non-English rendering are untested by these patch tests.

## Applying

Apply these entries only as part of the **whole ordered 5437 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches in isolation.

## Gotchas

`managed` and `available` answer different questions. A successful force request acknowledges a handoff, not an installed version or a completed restart. The no-pending informational result uses `ok: false` with `managed-by-hermes-sync` while showing `synced`, not an error dialog.

A stale or unreadable management record restores legacy routing. A managed backend's ordinary-update refusal protects older clients but does not itself submit a force request. Packaged client updates remain separate from core updates. English fallback registration is not translation completion.

## Why core instead of a plugin

Update ownership spans Desktop's apply stores, typed API scope, terminal overlay stages and backend update admission. Core must select one updater before launching either path and retain a server-side refusal for older clients; a plugin cannot reliably enforce that cross-version boundary.
