# Desktop alphabetical projects and row Archive placement

Mirror entries: [2319](patches/2319-fix-desktop-put-the-row-s-hover-Archive-button-at-the-right-edge-after-the-kebab-daveey.patch), [2320](patches/2320-feat-desktop-sort-sidebar-projects-alphabetically-and-hide-empty-ones-daveey.patch), [2321](patches/2321-fix-desktop-keep-an-entered-empty-auto-project-in-the-alphabetical-sidebar-daveey.patch).

## Behavior

- Put the hover Archive button at the row's right edge, after the session-actions kebab.
- Sort projects by name with Home first, numeric/case-insensitive label ordering and deterministic ID tie-breaking, ignoring activity, hoist and saved drag order.
- Hide empty projects (including explicit projects), but retain Home and the entered project, including an empty auto project; disable drag reorder while alphabetical policy is active.

## How it works

session-row-hover-actions renders the menu before Archive in one hover cluster. project-filter bundles the fork's alphabetical policy and strict empty filtering; index applies it to backend project-tree membership rather than deriving projects again. projects/model sorts labels with numeric locale comparison. 2321 runs the strict filter directly so an entered empty auto project is not removed by an earlier session-bearing filter before keepId can retain it.

## Code

- `apps/desktop/src/app/chat/sidebar/session-row-hover-actions.tsx`
- `apps/desktop/src/app/chat/sidebar/index.tsx`
- `apps/desktop/src/app/chat/sidebar/project-filter.ts`
- `apps/desktop/src/app/chat/sidebar/projects/model.ts`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `apps/desktop/src/app/chat/sidebar/session-row-actions.test.tsx`
- `apps/desktop/src/app/chat/sidebar/chat-sidebar.integration.test.tsx`
- `apps/desktop/src/app/chat/sidebar/project-filter.test.ts`
- `apps/desktop/src/app/chat/sidebar/projects/model.test.ts`

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

Unknown session counts behave as zero under the strict filter. The entered-project exception avoids bouncing to overview but does not invent backend membership. Component/integration assertions cover this code; no packaged Desktop build, screenshot or live UI inspection was performed in this pass.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
