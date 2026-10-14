# ⌘R restarts the backend and reloads the window

Patch: `0064`

## Behavior

**⌘R** restarts the Python backend for the profile you're viewing, then reloads the window. Edits to Hermes core
or to an already-loaded Python plugin take effect without quitting and relaunching the app.

- Restarting the backend interrupts every turn it is running, in every chat on that profile. Interrupted turns
  resume the way any backend restart does (see [auto-continue-resume.md](auto-continue-resume.md)).
- On a remote gateway, Hermes can't restart that machine's backend. ⌘R reconnects to it and then reloads.
- If the restart fails, you get an error toast and the window reloads anyway. A fresh window starts a
  backend that has died.
- Pressing ⌘R again while a restart is running does nothing.
- A focused browser pane or terminal still gets ⌘R first, so it reloads the page.
- **Moved bindings:** `session.resume` (send "resume" to the active chat) moves from ⌘R to **⌥⌘R**. ⇧⌘R
  (Electron `forceReload`) still reloads only the window.

## How it works

Electron's main process takes ⌘R before the renderer sees it and sends the preview-nav `reload` command. 0022's
fallback in `use-desktop-integrations.ts` runs whatever action is bound to `mod+r`. That action is now
`app.reloadAll`. It calls `reloadAppAndBackend()`, which awaits
`window.hermesDesktop.recycleBackend(profile)` before calling `window.location.reload()`. `recycleBackend` is
the existing IPC behind the Models page's "Restart backend" button. It kills the SSH-owned serve if there is
one, then the local child, and the app respawns it. A remote connection goes through `reconnectGateway()`
instead. A module-level flag drops repeat presses until the restart finishes.

## Code

- `apps/desktop/src/store/reload-all.ts`: `reloadAppAndBackend(reload?)`
- `apps/desktop/src/lib/keybinds/actions.ts`: `app.reloadAll` (`mod+r`); `session.resume` moved to `mod+alt+r`
- `apps/desktop/src/app/hooks/use-keybinds.ts`: handler
- `keybinds.actions['app.reloadAll']` labels in en/de/es/fr/ru/zh/ar_chrome (ja/zh-hant in `overlay-gaps.json`)

## Tests

- `src/store/reload-all.test.ts`: four cases. The local backend restarts before the reload. A remote gateway
  only reconnects. A failed restart still reloads and shows an error. A second press during a restart is
  ignored.
- `src/lib/keybinds/actions.test.ts`: `mod+r` maps only to `app.reloadAll`, `session.resume` is on
  `mod+alt+r`, both work from the composer, and nothing claims `mod+shift+r`.

## Applying

Apply this after 0022, which reroutes ⌘R through the keybind registry. `git am -3` it onto a tree that already
has 0022.

## Plugin?

No for ⌘R itself, for the same reason as 0022: the main process takes ⌘R first, and only the core `reload`
command handler can reroute it. A desktop plugin could offer "restart backend + reload" on another chord by
calling `window.hermesDesktop.recycleBackend()` and then `location.reload()`.
