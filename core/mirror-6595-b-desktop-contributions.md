# Sized titlebar tools and Sessions tab-strip contributions

Patch coverage: 6278, 6338, 6344, 6345.

## Behavior

Contributed titlebar tools can occupy several control cells without overlapping header text or being covered by the window drag region. A sessions.tabStrip contribution area places plugin chrome beside Sessions/Bots only in the Sessions zone.

Overflowing tab-strip chips remain horizontally reachable, with an empty area adding no box.

## How it works

A span is converted to a whole bounded cell count and used both for rendered tool width and reserved cluster padding. The panel-titlebar measurement reserves contributed pane tools in the titlebar band as well as fixed controls. Route/tool-identity changes trigger remeasurement even without a panel resize.

The Sessions slot filters renderable contributions, orders them through the contribution host and wraps each in its boundary. Width is capped relative to the strip; overflow scrolls from the leading edge rather than unsafe end alignment. Pointer events stay outside the pane drag handler. A browser test compiles the production utility classes to check first/last chip reachability.

## Code

Affected implementation and integration paths in the source series:

- `apps/desktop/src/app/contrib/wiring.tsx`
- `apps/desktop/src/app/shell/titlebar-controls.tsx`
- `apps/desktop/src/app/shell/titlebar.ts`
- `apps/desktop/src/components/pane-shell/tree/renderer/panel-titlebar.ts`
- `apps/desktop/src/components/pane-shell/tree/renderer/sessions-tab-strip.tsx`
- `apps/desktop/src/components/pane-shell/tree/renderer/tree-group.tsx`
- `apps/desktop/src/sdk/areas.ts`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `apps/desktop/src/app/shell/titlebar-tool-span.test.ts`
- `apps/desktop/src/components/pane-shell/tree/renderer/panel-titlebar.test.tsx`
- `apps/desktop/src/components/pane-shell/tree/renderer/sessions-tab-strip.test.tsx`
- `apps/desktop/e2e/sessions-tab-strip.spec.ts`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

span is layout metadata, not arbitrary CSS width. A tools cluster below the titlebar band must not reserve horizontal drag space there. Host contributions still need plugin registration; adding the area does not supply any actual chip by default.

## Why core, not a plugin?

Window drag geometry, pane layout and contribution mounting belong to the Desktop host; plugins need these core extension points to render safely.
