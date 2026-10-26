# ⌘⇧K opens the Kanban board

## Behavior
- **⌘⇧K** (Ctrl+Shift+K) opens the Kanban board (`/kanban`) from anywhere, including while typing in the composer.
  It is the `kanban.open` action: rebindable in Settings → Keybinds (Navigation), and the ⌘K palette row
  "Kanban: Open board" shows the live chord.
- **Moved binding:** the composer's "send next queued message" (`composer.sendQueued`) moves from ⌘⇧K to **⌘⌥K**.

## How it works
The bundled kanban desktop plugin registers a `KEYBINDS_AREA` contribution `kanban.open` (default `mod+shift+k`)
next to its existing `kanban.newTask` (⌘⌥N), and its palette row gains `action: 'kanban.open'`. The composer's
hard-coded keydown check now matches Cmd/Ctrl+Alt+K via `event.code === 'KeyK'` (Option turns `key` into `˚` on macOS).

## Code
- `apps/desktop/src/plugins/kanban/plugin.tsx` — keybind + palette `action`
- `apps/desktop/src/app/chat/composer/index.tsx` — send-queued chord
- `apps/desktop/src/app/chat/composer/help-hint.tsx`, `apps/desktop/src/lib/keybinds/actions.ts` — read-only rows

## Tests
Existing suites: `npx vitest run src/plugins/kanban src/lib/keybinds src/app/chat/composer` (129 pass); `tsc --noEmit`, eslint.

## Applying
`git am -3 --empty=keep core/patches/0056-*.patch`. The Kanban desktop plugin must be enabled (Capabilities ▸ Plugins), otherwise
⌘⇧K does nothing.

## Gotchas
- The keybind only exists while the kanban plugin is enabled (it ships `defaultEnabled: false`).

## Plugin?
The open-board half is pure plugin-SDK code and could live in a separate desktop plugin; the composer chord move
can't, since that check is hard-coded in core. Kept as one patch so ⌘⇧K never fires both.
