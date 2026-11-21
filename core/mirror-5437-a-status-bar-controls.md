# Hideable Desktop status-bar controls, versions off by default

Mirror entry: 5426.

## Behavior

- Client and server version pills are no longer locked visible; they are ordinary hideable controls and default to hidden.
- Command Center remains the only locked entry, as the recovery path.
- Every status-bar contribution, including old render-only plugins, gets a context-menu checkbox, labelled by explicit `toggleLabel`, then text label/title, then a humanized stable ID. Plugins cannot lock themselves visible.
- Connection/profile switcher custom renders are optional. Plan-usage and system-resource toggles stay discoverable while their contextual rendering and polling gates remain.
- Reset hides the versions again. Updater checks, progress and apply machinery, and the Command Center update action, are unchanged.

## How it works

Hidden-control preferences migrate from storage key v2 to v3 by adding just the two version IDs to the hidden list. The v1 approval migration, unrelated choices, and later explicit opt-ins (including an empty hidden list) are preserved; v3 is registered as a shared UI preference key.

`useStatusbarContributions` merges declarative data into render contributions, preserves declared IDs and preferences (falling back to the registry ID), keeps render metadata, forces `lockedVisible` false, and filters hidden items before mounting any custom render. Contextual availability stays distinct from the user's persistent enabled preference.

## Code

- `apps/desktop/src/store/statusbar-prefs.ts`, `apps/desktop/src/lib/ui-preferences/keys.ts`
- `apps/desktop/src/app/contrib/panes.tsx`
- `apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx`
- `apps/desktop/src/app/shell/statusbar-controls.tsx`
- `apps/desktop/src/app/shell/system-resources-statusbar.tsx`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Tests cover the v2-to-v3 migration and persistence across module reload, production item construction showing that only Command Center is locked, the context menu enabling independent version toggles, a menu entry for every contribution with custom renders filtered before mount, and persistence of toggles. Installed-renderer behavior is untested.

- `apps/desktop/src/store/statusbar-prefs.test.ts`
- `apps/desktop/src/app/contrib/panes.test.tsx`
- `apps/desktop/src/app/shell/hooks/use-statusbar-items.test.tsx`
- `apps/desktop/src/app/shell/statusbar-context-menu.test.tsx`
- `apps/desktop/src/app/shell/statusbar-visibility.test.tsx`

## Applying

Apply these entries only as part of the **whole ordered 5437 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches in isolation.

## Gotchas

Users who previously saw version pills will find them hidden after migration and must re-enable them from the status-bar menu. With versions hidden, update awareness relies on the updater and Command Center rather than a permanent pill. Unit fixtures that omit production item construction can hide disabled-control bugs.

## Why core instead of a plugin

The status bar, its preference store and migration, and the context menu live in the Desktop renderer core; plugin contributions are filtered through them.
