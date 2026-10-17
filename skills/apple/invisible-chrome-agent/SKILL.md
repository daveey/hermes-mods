---
name: invisible-chrome-agent
description: "Use when Hermes should drive a real Chrome the user never sees. Virtual display + CDP supervisor."
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
  notification "Hermes needs Touch ID — Tap Touch ID to sign in with a passkey for <rpId>" (osascript, so it shows as
  Script Editor). Conditional mediation (passkey autofill on page load) is ignored. This needs `Page.enable` +
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

hermes-chrome-agent status     # supervisor, displays, every Chrome window + URL
hermes-chrome-agent show       # windows → main display, re-homing paused (look at / click the page)
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

- **Hidden tabs can stall.** Background tabs in a headed Chrome get throttled; even with the flags and savers off,
  CDP calls into a non-selected tab were seen to hang for ~20-60s at a time (also in a fresh Chrome without this
  supervisor), then recover. Upstream: NousResearch/hermes-agent#135435. If a session times out on
  `Runtime.evaluate`, `Target.activateTarget` on its tab unsticks it.
- **Security:** anything running as the user can drive port 9333: read and act as every account logged in to the agent
  profile, and trigger passkey requests (which still need a Touch ID tap). The port binds to loopback only; nothing
  stops other local processes.
- Bluetooth (phone/hybrid passkeys, security keys over BLE) is unavailable: Chrome logs "responsible app ... does not
  have Bluetooth metadata"; it is launched by launchd, not Finder. iCloud Keychain passkeys work.
- This Chrome shows a second Chrome icon in the Dock / app switcher; activating it does not move its windows.
- Chrome restarts lose open tabs (no session restore, by design).
- Don't run a second Chrome on `$HERMES_HOME/chrome-agent`; the supervisor stops any stale one on startup.
- `CGDisplayCreateImage` is unavailable in the macOS 15+ SDK; use ScreenCaptureKit or `screencapture`.
