# Desktop: Chromium geometry test for the main-pane top strip

Mirror snapshot 5406; ordinals 5340, 5341.

Entries:

- **5340**: test(desktop): measure main-pane strip layout in Chromium
- **5341**: docs(desktop): list the main-pane strip layout spec in the core e2e README

## Behavior

- **5340**: required core E2E coverage mounting the production shell workspace, layout tree, registry and CSS with fake sidebar/main/terminal content; measures absent, visible and null contributions and titlebar clearance with the sidebar hidden. Earlier jsdom tests checked structure, not geometry.
- **5341**: documentation entry for the spec in the core e2e README.
- No production behavior change.

## How it works

- `apps/desktop/e2e/core/README.md` is the only non-test path shipped.

## Code

Shipped non-test paths (union over these entries):

- (none: these entries ship test files only)

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `apps/desktop/e2e/core/main-pane-top-layout.spec.ts`
- `apps/desktop/e2e/fixtures/main-pane-top-layout.html`
- `apps/desktop/src/test/main-pane-top-layout.fixture.tsx`
- `apps/desktop/e2e/core/README.md`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- The spec uses a Vite fixture page rather than the full Electron app, so it measures layout only.

## Why core instead of a plugin

The component under test is part of the core desktop shell.
