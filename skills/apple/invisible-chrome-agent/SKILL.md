---
name: invisible-chrome-agent
description: "Use when driving the hidden agent Chrome, or a Mac app won't come to the front. Virtual display + CDP supervisor."
version: 1.0.0
author: Claude Code
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [chrome, cdp, browser, virtual-display, passkeys, webauthn, launchd, macos]
    category: apple
    related_skills: [local-chrome-remote-debugging]
---

# Invisible Chrome agent (macOS)

A real Google Chrome that Hermes drives over CDP, rendered on a private-API virtual display
("Hermes Agent Display") so it never covers the user's screen. Real Chrome, not Chromium, matters:
Google Chrome carries Apple's `com.apple.developer.web-browser.public-key-credential` entitlement,
so iCloud Keychain passkeys work in it. A human still has to tap Touch ID for every passkey operation.

## Pieces (all in `scripts/`)

| File | What |
|---|---|
| `vdisplay.swift` + `CGVirtualDisplay.h` | `hermes-vdisplay`: creates a 1440x900 `CGVirtualDisplay` (override with `HERMES_VDISPLAY_SIZE=WxH`) that lives while the process runs. Parks it at the bottom-right corner of the desktop arrangement (corner contact only, so the cursor rarely wanders in), re-parks it if a monitor change makes it share an edge, and prints `display <id> bounds x,y WxH main x,y WxH` at start and on every change. |
| `hermes-chrome-agent` | Stdlib-only Python supervisor + CLI (runs under `/usr/bin/python3`, ships its own minimal CDP websocket client). |
| `notifier.swift` | `Hermes Touch ID.app` (bundle `com.hermes.chrome-agent.notify`, LSUIElement, Developer-ID signed when the keychain has one), built by `install`: posts the passkey notification with UNUserNotificationCenter and reports clicks back to the supervisor over stdout. |
| `test_hermes_chrome_agent.py` | Unit tests for the click-through path (`/usr/bin/python3 -m unittest -v test_hermes_chrome_agent` in `scripts/`). |

`hermes-chrome-agent run` (what launchd runs):
- starts `hermes-vdisplay`, then Chrome with `--user-data-dir=$HERMES_HOME/chrome-agent --remote-debugging-port=9333
  --remote-debugging-address=127.0.0.1 --remote-allow-origins=http://127.0.0.1:9333,http://localhost:9333`, the three
  anti-backgrounding flags, and Memory Saver / Energy Saver forced off in `Local State` (hidden agent tabs otherwise
  get frozen or discarded);
- every second: `Target.getTargets` → `Browser.getWindowForTarget` for every page, and `Browser.setWindowBounds` for
  any window not inside the virtual display (new windows, popups, OAuth windows land there within ~1s).
  `--window-position` alone is ignored by macOS, so this loop is what actually keeps windows hidden;
- restarts only the child that died: Chrome crash → relaunch (with backoff; its tabs are lost); vdisplay crash →
  new display, windows re-homed. A stale Chrome left on the profile (SingletonLock pid) is stopped first, since a
  second launch on the same profile would just hand off to it;
- auto-attaches to every page target and injects a wrapper around `navigator.credentials.get/create`
  (`Page.addScriptToEvaluateOnNewDocument` + `Runtime.addBinding`). A non-conditional WebAuthn request posts a macOS
  notification "Hermes needs Touch ID — Tap Touch ID to sign in with a passkey for <rpId>" from
  `Hermes Touch ID.app`. Clicking the notification or its **Show** button moves the window holding the asking tab
  onto the main display, `Target.activateTarget`s that tab, and brings Chrome forward (the notifier, frontmost after
  the click, hands activation to Chrome's pid; JXA as backup). Re-homing skips that window until the request
  succeeds (+3s grace) or 180s pass. A failed request (a hidden tab's `get` usually fails at once with
  NotAllowedError; sites retry) keeps the notification and the revealed window. One live notification per tab.
  There is deliberately no osascript fallback: `display notification` belongs to Script Editor, and clicking it
  opens Script Editor and shows nothing (the original bug). Notifications not allowed for Hermes Touch ID → the
  asking window is revealed on the main display at once (no activation) and the app shows a floating alert
  (Show / Dismiss / Notification Settings…) whose Show works like the notification's. App missing → window
  revealed and Chrome raised at once. Supervisor start and `status` print the permission and, when it is off,
  the deep link `x-apple.systempreferences:com.apple.Notifications-Settings.extension?id=com.hermes.chrome-agent.notify`.
  Conditional mediation (passkey autofill on page load) is ignored. This needs `Page.enable` +
  `Runtime.enable` on each page session: without them the script and binding vanish on the first cross-site
  navigation.
- `HERMES_CHROME_AGENT_SHOW_ON_PASSKEY=1` additionally moves the requesting window to the main display until the
  request settles (max 120s), so the human sees which site they approve. Off by default: the macOS passkey sheet
  (a 448-px sheet on the Chrome window, on the virtual display) accepts a Touch ID tap without being seen.

## Install / operate

```bash
S=~/code/hermes-mods/skills/apple/invisible-chrome-agent/scripts/hermes-chrome-agent
HERMES_CHROME_AGENT_LABEL=com.example.hermes-chrome-agent $S install   # builds hermes-vdisplay into
#   $HERMES_HOME/chrome-agent-tools/, symlinks the supervisor there, writes + bootstraps the LaunchAgent
#   (RunAtLoad, KeepAlive, log $HERMES_HOME/logs/chrome-agent.log). Default label com.hermes.chrome-agent.
hermes config set browser.cdp_url http://127.0.0.1:9333     # every Hermes session now attaches here

hermes-chrome-agent status     # supervisor, displays, notifier permission, every Chrome window + URL
hermes-chrome-agent show       # windows → main display, re-homing paused (look at / click the page)
hermes-chrome-agent show --minutes 20   # same, auto-hides after 20 min (supervisor restart needed to pick up)
hermes-chrome-agent hide       # resume: windows go back within ~1s
launchctl kickstart -k gui/$UID/<label>                    # restart (graceful: SIGTERM → Chrome and display stop)
tail -f ~/.hermes/logs/chrome-agent.log
```

Sign the agent into sites once: `hermes-chrome-agent show`, log in by hand in that window, `hide`.

## How Hermes uses it

`browser.cdp_url` is the operator override in `browser_exec` routing (`tools/browser_use_cli.py`
`_resolve_backend_cdp`): it wins over cloud providers and the managed Chromium, and it disables the
real-profile route (`_resolve_real_profile_cdp` returns early when an override is set). Consequences:
- Every surface that reads this profile's config (CLI one-shots, Desktop chats, Kanban workers, cron jobs,
  gateway) drives this Chrome. The value is read per call, so running backends pick it up without a restart.
- `browser_exec(local=true)` no longer launches a snapshot copy of the user's default Chrome profile; it attaches to
  this Chrome too. The agent profile has only the logins made in it, not the user's everyday cookies.
- `browser.use_real_profile` can stay on; it is simply shadowed. Unset `browser.cdp_url` to go back.
- Sessions are tabs in this one browser. Each `browser_exec` session gets its own tab; tabs persist until closed.

## Verify

- `curl -s 127.0.0.1:9333/json/version` answers; `hermes-chrome-agent status` lists windows at the virtual display's
  origin.
- Window placement without Screen Recording permission: `CGWindowListCopyWindowInfo` bounds per owner pid (a tiny
  Swift tool) show Chrome only on the virtual display. `screencapture -D 2` needs Screen Recording for the calling
  process and shows only wallpaper while the screen is locked (`lsappinfo front` = loginwindow).
- New window: `Target.createTarget {newWindow: true, left: 100, top: 100}` → re-homed in < 0.5s (log line `rehome:`).
- Kill tests: `kill -9 <chrome pid>` → CDP back within ~1-2s; `kill -9 <vdisplay pid>` → new display id, Chrome
  windows on it.
- Passkey: on webauthn.io, Authenticate → log `webauthn get request from https://webauthn.io`; the unified log shows
  NotificationCenter receiving it (`log show --predicate 'process == "NotificationCenter"'`; "muted by display state"
  just means the screen was locked).

## Pitfalls

- **Chrome's tab-modal dialogs don't follow a window to another display.** "Use a saved passkey for <site>" is
  a child window; after `Browser.setWindowBounds` moves its window to the main display it stays clamped to the
  virtual one, so the revealed page shows dimmed with no dialog. `place()` therefore nudges the width (-1, then
  back), which re-anchors the dialog on the window. Check with CGWindowList: the 448x387 Chrome window must sit
  inside the revealed window's bounds.

- **Notification permission is asked once per bundle id, and a withdrawn prompt counts as denied.** If the
  helper quits while macOS's "Hermes Touch ID Notifications" prompt is up, the app ends up denied (status
  `denied`) and only System Settings > Notifications > Hermes Touch ID can fix it. The helper therefore stays alive
  until the prompt is answered, and its timeout starts only once the notification is posted. Notification
  settings read `denied` while that first prompt is still pending, so status can't tell "pending" apart.
- **Hidden tabs can stall.** Background tabs in a headed Chrome get throttled; even with the flags and savers off,
  CDP calls into a non-selected tab were seen to hang for ~20-60s at a time (also in a fresh Chrome without this
  supervisor), then recover. If a session keeps timing out on `Runtime.evaluate`/`Page.navigate`, a
  `Target.activateTarget` sent through the harness may not help: the harness stays attached to its own (stalled)
  tab. What works: `curl 127.0.0.1:9333/json/close/<id>` the stalled tabs, `curl -X PUT
  '127.0.0.1:9333/json/new?<url>'`, `curl 127.0.0.1:9333/json/activate/<new id>`, then in the harness
  `switch_tab("<new id>")` before any `js()`.
- **Other apps' windows can land on the virtual display, and nothing moves them back.** macOS treats it as a real
  monitor and the re-home loop only touches Chrome. Symptom: the user "can't switch to" an app that is frontmost
  (`lsappinfo front`) and idle (`sample` shows the main thread in the event loop), and force-quit/relaunch doesn't
  help because it reopens there. Diagnose with CGWindowList bounds for the app's pid: its main window sits inside the
  "Hermes Agent Display" rect (`system_profiler SPDisplaysDataType`). Fix: compile `scripts/move-app-windows.swift`
  and run it once from a no-agent gateway cron (`hermes cron create 1m --script <x>.sh --no-agent --repeat 1
  --deliver local`), since only the gateway holds Accessibility; check the log says `trusted true` and re-read the
  bounds. Killing `hermes-vdisplay` doesn't evict them: the supervisor recreates the display at the same spot within
  ~1 s. Rewriting the app's `NSWindow Frame` default doesn't either.
- **Security:** anything running as the user can drive port 9333: read and act as every account logged in to the agent
  profile, and trigger passkey requests (which still need a Touch ID tap). The port binds to loopback only; nothing
  stops other local processes.
- Bluetooth (phone/hybrid passkeys, security keys over BLE) is unavailable: Chrome logs "responsible app ... does not
  have Bluetooth metadata"; it is launched by launchd, not Finder. iCloud Keychain passkeys work.
- This Chrome shows a second Chrome icon in the Dock / app switcher; activating it does not move its windows.
  Raising the app or `setWindowBounds` onto the main screen just flashes the window: the 1s re-home loop pulls it
  straight back. Show pages the way the next section describes.
- `show --minutes N` expiry is enforced by the supervisor's re-home loop, so it needs a supervisor restart to take
  effect, and a restart closes every tab. Never restart it while a tab holds a login or an in-flight task the user
  still needs.

## Showing the user a page (sign-in, 2FA, click)
The user wants the window brought to them, never "find the Chrome window" instructions, and sent back once done.
1. Call `browser_focus` (browser-focus plugin; `match` = URL/title substring). It runs `show --minutes 20`, activates
   the tab over CDP and raises the app. Without the plugin: `hermes-chrome-agent show`, then
   `curl 127.0.0.1:9333/json/activate/<id>` and JXA `NSRunningApplication...activateWithOptions(3)` on Chrome's pid.
2. If the harness keeps timing out (`Runtime.evaluate`/`Page.navigate` on background tabs), skip it: open the page
   with `curl -X PUT 'http://127.0.0.1:9333/json/new?<url>'` and activate that id. Let the user click the first
   button themselves.
3. Tell them it's in front and what to click. Detect completion by polling `/json/list` for the tab leaving its login
   URL (the plugin's watcher hides automatically); otherwise `browser_focus {hide:true}` / `hermes-chrome-agent hide`.
   The Desktop status bar has a Browser shown/hidden toggle (desktop-plugins/browser-focus) for manual control.
4. Verify placement from `CGWindowListCopyWindowInfo` bounds for Chrome's pid (on the main display, not the virtual
   one) and the frontmost pid, not from the activate call's return value.
- Chrome restarts lose open tabs (no session restore, by design).
- Don't run a second Chrome on `$HERMES_HOME/chrome-agent`; the supervisor stops any stale one on startup.
- `CGDisplayCreateImage` is unavailable in the macOS 15+ SDK; use ScreenCaptureKit or `screencapture`.
