# ⇧⌘R restarts the backend and reloads the window

Patches: `0064` (on ⌘R), `0071` (moved to ⇧⌘R; ⌘R is resume again)

## Behavior

**⇧⌘R** restarts the Python backend for the profile you're viewing, then reloads the window. Edits to Hermes core
or to an already-loaded Python plugin take effect without quitting and relaunching the app.

- Restarting the backend interrupts every turn it is running, in every chat on that profile. Interrupted turns
  resume the way any backend restart does (see [auto-continue-resume.md](auto-continue-resume.md)).
- On a remote gateway, Hermes can't restart that machine's backend. ⇧⌘R reconnects to it and then reloads.
- If the restart fails, you get an error toast and the window reloads anyway. A fresh window starts a
  backend that has died.
- Pressing ⇧⌘R again while a restart is running does nothing.
- A focused browser pane or terminal gets ⇧⌘R first.
- **Moved bindings (0071):** ⌘R is `session.resume` again (send "resume" to the active chat; 0064 had moved
  it to ⌥⌘R). Electron's `forceReload` (window-only reload, the escape hatch) moves from ⇧⌘R to **⌥⇧⌘R**.

## How it works

0071 gives the View menu's `forceReload` item an explicit `Alt+Shift+CommandOrControl+R` accelerator, so no menu
accelerator eats ⇧⌘R and it reaches the renderer keybind `app.reloadAll` (`mod+shift+r`). ⌘R still goes through
0022's main-process reroute, which runs whatever is bound to `mod+r` (`session.resume` again). `app.reloadAll` calls `reloadAppAndBackend()`, which awaits
`window.hermesDesktop.recycleBackend(profile)` before calling `window.location.reload()`. `recycleBackend` is
the existing IPC behind the Models page's "Restart backend" button. It kills the SSH-owned serve if there is
one, then the local child, and the app respawns it. A remote connection goes through `reconnectGateway()`
instead. A module-level flag drops repeat presses until the restart finishes.

## Code

- `apps/desktop/src/store/reload-all.ts`: `reloadAppAndBackend(reload?)`
- `apps/desktop/src/lib/keybinds/actions.ts`: `app.reloadAll` (`mod+shift+r`); `session.resume` on `mod+r`
- `apps/desktop/electron/main.ts`: View menu `forceReload` accelerator ⌥⇧⌘R
- `apps/desktop/src/app/hooks/use-keybinds.ts`: handler
- `keybinds.actions['app.reloadAll']` labels in en/de/es/fr/ru/zh/ar_chrome (ja/zh-hant in `overlay-gaps.json`)

## Tests

- `src/store/reload-all.test.ts`: four cases. The local backend restarts before the reload. A remote gateway
  only reconnects. A failed restart still reloads and shows an error. A second press during a restart is
  ignored.
- `src/lib/keybinds/actions.test.ts`: `mod+r` maps only to `session.resume`, `mod+shift+r` only to
  `app.reloadAll`, both work from the composer, and nothing claims `mod+alt+r`.

## Applying

Apply 0064 then 0071 after 0022, which reroutes ⌘R through the keybind registry.

## Plugin?

Partly. The action could live in a desktop plugin on ⇧⌘R only if core also drops the `forceReload` menu
accelerator (0071 does). A desktop plugin could offer "restart backend + reload" on another chord by
calling `window.hermesDesktop.recycleBackend()` and then `location.reload()`.
