# Desktop: cancellable task-pill click and paged Archived view

Patches: **4733, 4734**

- 4733 (`498ee82c2a`): feat(desktop): a plugin can take over the status-bar task pill's click [daveey]
- 4734 (`2f0663b599`): fix(desktop): page the Archived view instead of reading the whole archive

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- The status-bar task pill dispatches a cancellable `hermes-kanban-chip` window event and opens the board only if no listener prevented it. Core names no plugin.
- The Archived sidebar view and Settings archived sessions load one page at a time with a load-more row, instead of reading the entire archive first. Refresh re-reads as many pages as were loaded; edits in place restart from the top because server offsets shift; a still-loading view is not reported empty.

## How it works

`plugins/kanban/chip-click.ts` and `plugin.tsx`; `store/sidebar-archive.ts` (`createArchivedPager`), `archived-view.ts`, sidebar and settings components.

## Code

- `apps/desktop/src/plugins/kanban/chip-click.ts`
- `apps/desktop/src/plugins/kanban/plugin.tsx`
- `apps/desktop/src/app/chat/sidebar/archived-view.ts`
- `apps/desktop/src/app/chat/sidebar/index.tsx`
- `apps/desktop/src/app/settings/sessions-settings.tsx`
- `apps/desktop/src/store/sidebar-archive.ts`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `apps/desktop/electron/profile-session-routing.test.ts`: modified only
- `apps/desktop/src/app/chat/sidebar/chat-sidebar.integration.test.tsx`: `drops the pin hint in the Archived view when Pinned is empty`, `lists the first page and reads the next one from the load-more row, whatever the grouping`, `offers no load-more row once the archive is fully listed`, `does not call an archive that is still loading empty`
- `apps/desktop/src/app/settings/sessions-settings.test.tsx`: `lists the first page of a deep archive and reads the next on request`, `reads again from the top after an unarchive, so the row that moved up is not skipped`, `offers no load-more when the first page is the whole archive`, `leaves out a live pinned chat the server back-filled onto the page`
- `apps/desktop/src/plugins/kanban/chip-click.test.ts`: `opens the board when nobody takes the click over`, `does not open the board when a listener prevents the default`, `opens the board again once that listener is gone`
- `apps/desktop/src/store/sidebar-archive.test.ts`: `shows the first page of a deep archive without reading the rest`, `reports nothing more when the first page holds the whole archive`, `appends one page per load-more, from the next offset`, `re-reads every page the user loaded on a refresh`, `keeps the loaded rows and the offer when a load-more fails`, `reads again from the top when the list was edited before a load-more`, `reads again from the top when the list is edited while a load-more is reading`, `has a running refresh read the extra page when load-more is asked mid-refresh`, `does not count a load-more stranded by a scope round trip against the scope it returns to`, `drops the cursor on a scope switch, so the new scope starts at its first page`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Dedupe and stop rules match the previous full-load behavior.
- Deep-archive behavior depends on tests that use multi-page archives.
