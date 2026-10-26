# ⌘N on This device, ⌘⇧N on the current gateway

## Behavior
- **⌘N** (`session.new`) starts a new chat **on This device**. If another gateway (remote/cloud) is selected, the
  desktop switches the active source to the `local` connection (its last-used profile) and the fresh draft lands there.
  If This device is already selected it behaves like upstream ⌘N. If the local runtime isn't installed, the hotkey
  keeps the current gateway (it never launches the installer).
- **⌘⇧N** (`session.newSameGateway`, new) starts a new chat on whichever gateway is currently selected (upstream ⌘N).
- Rename (`session.rename`, patch 0020) moves from ⌘⇧N to **⌘⇧E**.

All three are rebindable in Settings → Keybinds.

## How it works
`use-keybinds.ts`: both handlers share `startNewSessionHere()` (workspace scope → sessions, clear the quick-create
profile, fresh draft). `session.new` additionally calls `selectThisDeviceForNewChat()` (`store/connections.ts`), which
probes `probeLocalBackend()` and then `selectConnection('local')`; the switch itself re-homes the draft.

## Code
- `apps/desktop/src/lib/keybinds/actions.ts` — defaults
- `apps/desktop/src/app/hooks/use-keybinds.ts` — handlers
- `apps/desktop/src/store/connections.ts` — `selectThisDeviceForNewChat`, `thisDeviceLabel`
- i18n: `session.newSameGateway` label in en/de/es/fr/ru/zh/ar overlays (ja, zh-hant in `overlay-gaps.json`)

## Tests
`src/app/hooks/use-keybinds-new-session.test.tsx`, `src/store/connections.test.ts` (`selectThisDeviceForNewChat`).

## Applying
Needs 0020 (it rebinds `session.rename`). `git am -3 --empty=keep core/patches/0042-*.patch`.

## Gotchas
- Users with a single connection see no difference between ⌘N and ⌘⇧N.

## Plugin?
No: keybind defaults and the built-in `session.new` handler are core renderer code a plugin can't shadow.
