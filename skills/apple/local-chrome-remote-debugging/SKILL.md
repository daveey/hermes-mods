---
name: local-chrome-remote-debugging
description: "Chrome with remote debugging on macOS for agent control."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [chrome, remote-debugging, cdp, browser, macos, default-browser]
    category: apple
---

# Local Chrome Remote Debugging (macOS)

Set up the user's real Chrome with `--remote-debugging-port=9222` so the agent
can drive it via CDP (`browser_exec(local=true)` or direct WebSocket). The
user's tabs, cookies, and logins must be present in the driven instance.

## Why this skill exists

`browser_exec(local=true)` launches a **separate managed Chrome** with a clean
profile at `~/.hermes/browser-profile/chrome` — NOT the user's real Chrome.
To drive the user's actual browser session (tabs, cookies, logins), you must
launch Chrome with `--remote-debugging-port` yourself and point it at the real
profile.

## The procedure

### 1. Create a wrapper .app

Chrome 155+ refuses remote debugging on the default data directory (prints
"DevTools remote debugging requires a non-default data directory" and does not
bind the port). The workaround: create a separate `--user-data-dir` that
symlinks the real `Default` profile.

Create `~/Applications/Chrome (Debug).app`:
- `Contents/MacOS/chrome-debug.sh` — bash wrapper that execs Chrome with
  `--remote-debugging-port=9222 --user-data-dir="$HOME/Library/Application Support/Google/Chrome-Debug"`.
- `Contents/Resources/app.icns` — copied from the real Chrome bundle for the dock icon.
- `Contents/Info.plist` — bundle ID `com.google.chrome.debug`, with
  `CFBundleURLTypes` declaring `http`+`https` schemes so LaunchServices
  recognizes it as a browser.

The `Chrome-Debug` data dir must contain:
- A symlink: `Chrome-Debug/Default` -> `Chrome/Default` (the real profile)
- A copy of `Chrome/Local State` (needed for profile discovery)

### 2. Register and set as default browser

```bash
# Register the .app with LaunchServices
lsregister ~/Applications/Chrome\ \(Debug\).app

# Install and use `defaultbrowser` (the ONLY tool that works on macOS 26)
brew install defaultbrowser
defaultbrowser        # lists available browsers; * marks the current default
# Set by SHORT NAME from the list (not bundle ID):
defaultbrowser debug  # sets com.google.chrome.debug as default HTTP handler
```

Verify: `duti -d https` should print `com.google.chrome.debug`.

### 3. Add to the Dock

Modify `~/Library/Preferences/com.apple.dock.plist` -> `persistent-apps` array,
appending a `file-tile` entry pointing at `~/Applications/Chrome (Debug).app/`.
Then `killall Dock`.

### 4. Verify

```bash
curl -s http://127.0.0.1:9222/json/version   # should return JSON with Browser version
curl -s http://127.0.0.1:9222/json/list      # lists all tabs
```

Or in Python: `urllib.request.urlopen('http://127.0.0.1:9222/json/list')`.

## Pitfalls

- **Chrome 155+ blocks remote debugging on the default data directory.**
  Passing `--user-data-dir=` pointing at the exact same `~/Library/Application
  Support/Google/Chrome` path does NOT work — Chrome detects it as the default
  and refuses. Must use a different directory with a symlinked `Default`.

- **`duti` cannot set the https handler on macOS 26.** It fails with error -50
  or -54 because macOS protects the default-browser scheme. Use `defaultbrowser`
  (brew) instead — its argument is the SHORT NAME from `defaultbrowser`'s list
  output (e.g. `debug`), not the bundle ID.

- **Stale `DevToolsActivePort` file.** If Chrome was killed without cleanup,
  `~/Library/Application Support/Google/Chrome/DevToolsActivePort` may contain
  an old port number with nothing listening. Remove it before relaunching and
  check the file's mtime — a stale file is days old.

- **Multiple Chrome instances compete for the singleton.** The Hermes managed
  Chrome (`~/.hermes/browser-profile/chrome`) and the user's real Chrome share
  the same app binary. Kill ALL Chrome processes (`killall "Google Chrome"` +
  all Helper variants) before relaunching with the debug flag, or the new
  process may not bind the port.

- **`open -a` may find a stale/broken .app.** If you created a test .app in
  `/Applications/` with a malformed Info.plist, remove it before relying on
  `open -a "Chrome (Debug)"` — LaunchServices may resolve to the broken copy.
