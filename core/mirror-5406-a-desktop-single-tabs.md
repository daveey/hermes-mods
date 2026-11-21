# Desktop Single main tabs and shell-wide top strip

Mirror entries: 5052, 5053, 5054, 5055, 5056, 5090.

## Behavior

- Appearance offers Single alongside Auto/Always/Never, with Single as the default. Single retains one main tab slot per group without closing displaced session owners; side/tool panes remain available.
- Opening/revealing another main tab replaces the visible slot; closing it can refill from displaced tabs. Leaving Single restores eligible displaced panes.
- Browser-preview-only zones keep all their tabs/chips. Releasing Single's park does not release another pane mirror's park.
- `shell.topStrip` contributions render across the shell above pane tabs. Titlebar space is reserved only when mounted contributions actually render positive height; pane bodies retain identity as the strip changes.
- Lane usage reporting includes per-lane `blocked_on_you` counts.

## How it works

`main-tab-policy.ts` shares pane eligibility between the renderer and keyboard slots. The tree store applies Single enforcement to opens, reveals, removal and restoration, with displaced-tab memory separated into a resettable module. Parking claims are per owner (`single` versus pane mirrors), so refill/release respects other owners. A shared preview-only predicate keeps policy, collapse and strip visibility aligned.

`ShellWorkspace` mounts ordered `TOP_STRIP_AREA` contributions outside the pane tree. The follow-up measures natural height and observes resizes rather than treating registration count as visible content. Null-rendering contributors remain mounted so asynchronous content may appear later. SDK docs and localized appearance labels describe the new area/mode; lane backend output adds the blocked count.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `apps/desktop/src/app/contrib/controller.tsx`
- `apps/desktop/src/app/settings/appearance-settings.tsx`
- `apps/desktop/src/app/shell/top-strip.tsx`
- `apps/desktop/src/components/pane-shell/tree/main-tab-policy.ts`
- `apps/desktop/src/components/pane-shell/tree/renderer/strip-visibility.ts`
- `apps/desktop/src/components/pane-shell/tree/renderer/tree-group.tsx`
- `apps/desktop/src/components/pane-shell/tree/renderer/use-main-tab-policy.ts`
- `apps/desktop/src/components/pane-shell/tree/single-tab-state.ts`
- `apps/desktop/src/components/pane-shell/tree/store.ts`
- `apps/desktop/src/i18n/ar_settings.ts`
- `apps/desktop/src/i18n/de.ts`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/es.ts`
- `apps/desktop/src/i18n/fr.ts`
- `apps/desktop/src/i18n/ja.ts`
- `apps/desktop/src/i18n/ru.ts`
- `apps/desktop/src/i18n/types.ts`
- `apps/desktop/src/i18n/zh-hant_settings.ts`
- `apps/desktop/src/i18n/zh.ts`
- `apps/desktop/src/sdk/areas.ts`
- `apps/desktop/src/store/tabstrip-prefs.ts`
- `hermes_cli/kanban_lanes.py`
- `locales/_keys.desktop.json`
- `website/docs/developer-guide/desktop-plugin-sdk.md`
- `apps/desktop/src/components/pane-shell/tree/single-tab-memory.ts`
- `apps/desktop/src/app/chat/pane-mirror.ts`
- `apps/desktop/src/components/pane-shell/tree/parked-panes.ts`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Single integration tests drive open-in-new-tab, draft creation, explicit reveal, close/refill and mode transitions through the real tree store. Policy/keyboard tests cover side panels, hidden/unregistered panes, and profile-switch fallback with a hidden Single main strip. Park-memory and preview-mirror tests cover reset isolation, concurrent park owners, registry changes and multiple Browser tabs. Top-strip tests cover ordering, null/late content, geometry, resize handling and stable pane body identity. Python lane tests cover blocked-on-user counts. These are source unit/component/integration fixtures; native Desktop rendering, actual plugin deployment and interactive visual QA are untested here.

Source test/coverage paths:

- `apps/desktop/src/app/chat/compact-tab-menu.test.tsx`
- `apps/desktop/src/app/chat/preview-session-focus.test.ts`
- `apps/desktop/src/app/chat/preview-tile.test.ts`
- `apps/desktop/src/app/chat/reuse-draft-tab.test.ts`
- `apps/desktop/src/app/chat/single-main-tab.integration.test.tsx`
- `apps/desktop/src/app/shell/top-strip-layout.test.tsx`
- `apps/desktop/src/app/shell/top-strip.test.tsx`
- `apps/desktop/src/components/pane-shell/tree/focused-session-tab.test.ts`
- `apps/desktop/src/components/pane-shell/tree/hovered-zone-tabs.test.ts`
- `apps/desktop/src/components/pane-shell/tree/lone-chat-sibling-zone.test.ts`
- `apps/desktop/src/components/pane-shell/tree/main-tab-policy.test.ts`
- `apps/desktop/src/components/pane-shell/tree/mode-layout-memory.test.ts`
- `apps/desktop/src/components/pane-shell/tree/pane-share-memory.test.ts`
- `apps/desktop/src/components/pane-shell/tree/renderer/collapse-restore-affordance.test.tsx`
- `apps/desktop/src/components/pane-shell/tree/renderer/compact-tab-picker.test.tsx`
- `apps/desktop/src/components/pane-shell/tree/renderer/strip-visibility.test.ts`
- `apps/desktop/src/components/pane-shell/tree/renderer/tab-close-affordance.test.tsx`
- `apps/desktop/src/components/pane-shell/tree/renderer/tree-group.test.tsx`
- `apps/desktop/src/components/pane-shell/tree/tab-key-hints.test.tsx`
- `apps/desktop/src/components/pane-shell/tree/tab-slot-shown.test.ts`
- `apps/desktop/src/store/session-states.test.ts`
- `apps/desktop/src/store/tabstrip-prefs.test.ts`
- `tests/hermes_cli/test_kanban_lanes.py`
- `apps/desktop/src/components/pane-shell/tree/single-tab-memory.test.ts`
- `apps/desktop/vitest.setup.ts`
- `apps/desktop/src/app/hooks/use-keybinds-tab-slot.test.ts`
- `apps/desktop/src/app/chat/single-tab-preview-park.test.ts`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

Single is not a session-close policy and does not collapse preview-only groups. A contribution that registers but renders null must not consume titlebar space. Tests that expect legacy multiple tabs must set the old mode explicitly and clear Single memory between runs.

## Why core instead of a plugin

Plugins can contribute to `shell.topStrip`, but the area host, titlebar geometry, tab visibility, keyboard slots and pane parking are core shell/store responsibilities. One plugin cannot enforce those invariants for every tab producer.
