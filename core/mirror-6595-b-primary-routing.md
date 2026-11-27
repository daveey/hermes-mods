# Primary reconnect ownership and exact sidebar list batching

Patch coverage: 6169, 6170, 6171, 6172, 6173, 6229, 6301, 6302, 6303.

## Behavior

Desktop reconnect and fresh WebSocket ticket minting retain the backend that owned the primary descriptor rather than borrowing a same-named local pool or the mutable foreground target. Explicit named requests remain explicit and registry-scoped requests retain their connection identity.

Unified sidebar refresh can obtain exact recents, cron and messaging list slices in one primary request and one annotation scope. A registry source that actually reuses that primary can reuse its clean aggregate without scanning the same backend again.

## How it works

An explicit boolean inheritsPrimary flag travels through renderer, preload and main-process IPC; both connection resolution and ticket minting use it. Local ticket recovery uses the resolved primary label. Registry aggregation reuses only an error-free primary result and copies rows before owner tagging.

The list_slices selector asks the sidebar endpoint for versioned list envelopes, with each slice retaining totals and profile totals. The merge consumes request-owned bases, not cross-refresh retained responses. Malformed/old batches, concrete profile selectors and unsupported windows fall back to the legacy paginated reads. Projection-manifest validation requires the explicit selector and validates each envelope.

## Code

Affected implementation and integration paths in the source series:

- `apps/desktop/electron/desktop-profile.ts`
- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/preload.ts`
- `apps/desktop/electron/profile-session-routing.ts`
- `apps/desktop/electron/registry-session-sources.ts`
- `apps/desktop/electron/sidebar-primary-list-batch.ts`
- `apps/desktop/src/app/gateway/hooks/use-gateway-boot.ts`
- `apps/desktop/src/global.d.ts`
- `apps/desktop/src/lib/gateway-ws-url.ts`
- `apps/desktop/src/lib/primary-reconnect-connection.ts`
- `hermes_cli/profile_session_list_slices.py`
- `hermes_cli/sessions_projection_manifest.py`
- `hermes_cli/web_routers/profiles.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `apps/desktop/electron/primary-reconnect-main.test.ts`
- `apps/desktop/src/app/gateway/hooks/use-gateway-boot-remote-target.test.tsx`
- `apps/desktop/electron/registry-session-sources.test.ts`
- `apps/desktop/electron/sidebar-primary-list-integration.test.ts`
- `tests/hermes_cli/test_profile_session_list_slices.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

A truthy non-boolean flag does not grant inheritance. A named request served by the primary is not thereby an inherited descriptor. Reuse is identity- and request-scoped; degraded reads must not become authoritative cached rows.

## Why core, not a plugin?

The Electron IPC owner routing and shared session-list annotation scope are host responsibilities; a disk plugin cannot safely replace either boundary.
