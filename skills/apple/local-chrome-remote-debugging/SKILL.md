---
name: local-chrome-remote-debugging
description: "Use when driving or handing off the agent's macOS Chrome."
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

Distinguish a managed browser from an attachment to the user's running Chrome.
Current Hermes real-profile browsing can copy the user's profile, including logins;
it is not necessarily a clean profile. Check the live browser documentation and
configuration rather than assuming `browser_exec(local=true)` attaches to the
running browser or always starts signed out.

### Reuse an existing connection first

1. If Chrome DevTools MCP is available, call `mcp__chrome_devtools__list_pages`
   before changing Chrome's launch configuration. A working connection avoids a
   browser restart and profile copying entirely.
2. Open only the requested service with `new_page(background=true)`, then read it
   with `take_snapshot(pageId=...)`. Use explicit page IDs: opening tabs changes
   selection, so concurrent work must not depend on the selected tab.
3. If navigation times out, inspect the created page before opening another tab;
   the page may have loaded despite the navigation timeout. After a click, verify
   the destination rather than treating a successful click as successful navigation.
4. If real-profile copying reports a locked SQLite database, try the existing
   DevTools connection before asking to quit Chrome. Do not raw-copy locked login
   databases or kill the user's browser to work around the copy failure.
5. Only proceed to new CDP setup below if no existing connection suffices. Changes
   to the default browser or Dock are optional customization, not prerequisites
   for accessing an account.

### Verify account access, not just browser access

For account-connection requests, verify a signed-in identity on the requested site.
Report browser access, authenticated account access, and tested ordering/checkout
as separate milestones; a personalized home page does not prove checkout works.
When multiple agents or hosts are requested, verify each access path separately:
one agent's authenticated browser is not evidence that another agent can use it.
Use a secure login flow or let the user sign in directly in the target browser;
never move passwords or verification codes into chat. Setup alone does not authorize
a test purchase.

### Handing a step to the user in the driven browser

When the user has to act in the browser the agent drives (sign in, 2FA, captcha,
click, approve), bring that window and the right tab to the front BEFORE asking,
then tell them it is already in front. Never make them hunt for the window. The
browser-focus plugin does this (tool `browser_focus`, plus hooks on sign-in pages
and on replies that ask for browser action); call the tool anyway when asking.

1. Find the driven Chrome: a main process (no `--type=`, no `--headless`) whose
   `--user-data-dir` is under the Hermes home, with `--remote-debugging-port`.
   Headless helper Chromes from other tools match the same pattern, so skipping
   `--headless` and preferring the instance holding the wanted tab are both needed.
2. Select the tab: `GET http://127.0.0.1:<port>/json/list` (most recently active
   first), then `GET /json/activate/<id>`.
3. Raise the app: JXA `$.NSRunningApplication.runningApplicationWithProcessIdentifier(pid).activateWithOptions(3)`
   via `osascript -l JavaScript`. It needs no Accessibility grant, unlike System Events.
4. Verify with `$.NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier`.

The driven browser is a separate profile copy, so a user who says "signed in" while
the driven tab still shows the sign-in page probably signed in to their everyday
Chrome. Re-check the driven tab's URL, raise it again, and say it is the window
that just came to the front, not the one they normally use.

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

- **Check process/profile ownership before relaunching Chrome.** An existing
  process using the intended data directory can receive the launch request instead
  of starting a new debugging listener. Reuse a working CDP attachment first; if a
  restart is necessary, get permission and quit gracefully so open user work is
  not lost. Sharing the Chrome executable alone is not proof of a profile conflict.

- **`open -a` may find a stale/broken .app.** If you created a test .app in
  `/Applications/` with a malformed Info.plist, remove it before relying on
  `open -a "Chrome (Debug)"` — LaunchServices may resolve to the broken copy.
