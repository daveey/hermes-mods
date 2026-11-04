# Desktop upstream compatibility: sibling exports and test mocks

Patches **1919** (`ecae29789957`) and **1922** (`1360ebb5baab`).

## Behavior

1919 preserves the SessionInfo and sidebar SDK export interfaces while moving fork-owned declarations out of files that crossed the size cap after upstream integration. 1922 updates two fork test mocks to match upstream's dial readiness and onboarding connection APIs; it changes no production behavior.

## How it works

`SessionInfo` extends `SessionActivityFields` from a sibling module. Sidebar chrome exports move into `sdk/sidebar-sections.ts` and remain re-exported by the SDK facade. The reconnect socket mock sends a legacy-peer ready frame after opening. The negotiated-image test's Hermes mock supplies `getApiRequestConnection`.

## Code

- `apps/desktop/src/types/hermes.ts`, `types/hermes-session-activity.ts`
- `apps/desktop/src/sdk/index.ts`, `sdk/sidebar-sections.ts`
- `apps/desktop/src/store/gateway-local-ticket-reconnect.test.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/remote-negotiated-image.test.tsx`

## Tests

The source commit for 1919 records typecheck, lint and SDK/sidebar Vitest checks. 1922 modifies the two test files above. This mirror pass verifies exact normalized source-tree equivalence and replay, but does **not** rerun Desktop typecheck, Vitest or a live UI flow; those paths remain untested by this pass.

## Applying

Use the README full-series recipe: the upstream bridge before 1919 supplies the new declarations and the bridge before 1922 supplies the newer Desktop APIs. Standalone application is not certified.

## Gotchas

Preserve facade re-exports: consumers still import the old public surface. A socket opening is no longer sufficient to model a ready public dial.
