---
name: macos-dock-management
aliases: [macos-dock, dock-cleanup]
description: Use when Dock icons are dead, badgeless or open wrong app.
category: apple
version: 1.0
author: hermes-curator
license: MIT
metadata:
  hermes:
    tags: [macos, dock, adware-cleanup, plist]
    related_skills: [apple-services, computer-use]
---

# macOS Dock management (dead icons, stale entries, pin changes)

## When to Use

- The Dock shows one or more `?` placeholder icons (dead pins for deleted apps).
- You need to pin, unpin, or reorder Dock entries from the terminal, headless and verifiably.
- A user asks "what is this icon / did something get launched" about a Dock anomaly.

## Procedure: diagnosing a "?" Dock icon

1. A `?` placeholder means the Dock has a persistent entry whose app bundle no longer exists on disk. Identify it:
   ```sh
   defaults read com.apple.dock persistent-apps | grep -E '_CFURLString|file-label'
   ```
   Pair each `_CFURLString` with its `file-label`; the dead ones are those whose `/Applications/<name>.app` path fails `ls -d`.
2. The small dot under a `?` icon is stale Dock state, NOT a running process — confirm with `ps aux | grep -i '<name>'` before telling the user anything was "launched". Adware/junk installers commonly leave these pins behind after their bundles are deleted; also check `~/Library/Application Support`, `~/Library/Caches`, and `~/Library/Preferences` for leftovers of the same name.

## Procedure: removing an entry reliably

1. Prefer editing the plist directly — this is the path that works. `dockutil --remove '<label>'` can silently do nothing (no error, entry still listed after), and any removal that does take can be reverted by the restart that applies it, because the running Dock keeps state in memory and rewrites `com.apple.dock.plist` from memory on `killall Dock`.
2. Direct edit, then restart, then verify:
   ```python
   import plistlib, os
   p = os.path.expanduser("~/Library/Preferences/com.apple.dock.plist")
   with open(p, 'rb') as f: dock = plistlib.load(f)
   def fields(d):
       for k, v in d.items():
           if isinstance(v, dict): yield from fields(v)
           elif k in ('_CFURLString', 'file-label'): yield v
   dock['persistent-apps'] = [a for a in dock['persistent-apps']
                              if not any('TERM' in str(x) for x in fields(a))]
   with open(p, 'wb') as f: plistlib.dump(dock, f)
   ```
   (replace `TERM` with the app name to remove)
3. Apply and verify in one go — verification AFTER the restart is mandatory, since the restart is the step that can undo the edit:
   ```sh
   killall Dock && sleep 2
   defaults read com.apple.dock persistent-apps | grep -ic 'TERM'   # must print 0
   ```
4. `dockutil` is not preinstalled: `brew install dockutil`. If used anyway, never trust its exit status — re-list with `dockutil --list | grep <label>`.

## Missing unread badge on a Dock icon

1. Ask the app what it is publishing: `lsappinfo info -only StatusLabel "<App>"`. A label (e.g. `"label"="9"`) means the app is setting a badge and macOS is hiding it, so the problem is system settings, not the app or its own preferences. Compare with an app whose badge does show.
2. Read the app's notification settings: `open "x-apple.systempreferences:com.apple.Notifications-Settings.extension?id=<bundle id>"` (bundle id from `osascript -e 'id of app "<App>"'` or `ncprefs.plist`), then read the page with an accessibility snapshot (e.g. BB Pod `ui.snapshot {"app":"System Settings"}`). If `allow-notifications` is off, the Dock badge is hidden even though `badge-app-icon-toggle` below it still reads on (it is greyed out). Turning on Allow notifications is the fix.
3. Also check Focus: a Focus mode that silences the app can hide badges too.

Pitfalls:
- Don't restart the Dock (`killall Dock`) for a missing badge: a published label that isn't drawn is a settings gate, and the restart only blanks every other app's badge for a moment.
- Look at what the user's screenshot actually shows before loading app-specific skills: a screenshot of another app's window with unread counts isn't evidence about the app's Dock badge.
- Background clicks (`ui.click` without `foreground`) on the System Settings Allow switch may do nothing, and a foreground click can report `confirmed` while the snapshot still reads off. Re-snapshot after every click. If the switch is still off and the user is working in another window, stop and tell them the exact switch to flip instead of repeatedly bringing Settings forward over their work.

## Duplicate icons for one app

- Two icons (one pinned, one in the recent/running section) mean the pin points at a different bundle than the running process — e.g. a launcher stub (`~/Applications/Hermes Launcher.app`, LSUIElement, just `open`s the dev build at `~/code/hermes-agent/apps/desktop/release/mac-arm64/Hermes.app`). Compare the pin's `_CFURLString` with `ps axo comm`. Fix by rewriting that tile in place: set `file-data._CFURLString` to the real bundle, `file-label`, `bundle-identifier`, and drop the stale `book` bookmark; then `killall Dock` and re-verify.
- The Hermes launcher (`com.daveey.hermes-launcher`) exists to run `hermes desktop --build-only` + a Developer ID team-signature check before opening `<runtime>/apps/desktop/release/mac-arm64/Hermes.app` (`com.nousresearch.hermes`), then exits. Two icons are inherent while the pin is the launcher, because the Dock keys tiles by bundle id. Repinning the real bundle gives one icon but skips the auto-rebuild/signature gate. When the user asks about the duplicate, do the repin by default and state the trade-off in one line. Don't offer an options menu, because the user wants the obvious fix executed.

## Dock Chrome opens the hidden agent Chrome ("Chrome stuck on the virtual screen")

1. From a remote gateway (hub), run every Mac command through `run_in_user_terminal` — it executes on the MacBook. Don't probe tailscale/ssh from the hub first; the hub has no key for the Mac and `browser_focus` there only sees the hub's own Xvfb Chrome.
2. `ps -axo pid,args | grep -E 'Contents/MacOS/Google Chrome( |$)'`. If the only main process has `--user-data-dir=~/.hermes/chrome-agent`, the user's Chrome isn't running at all: the Dock tile is keyed by bundle id, so clicking it activates the agent instance whose windows the supervisor re-homes to the virtual display. Moving windows won't help.
3. Fix: `open -n -a "Google Chrome" --args --user-data-dir="$HOME/Library/Application Support/Google/Chrome"`, then confirm window positions via System Events (`position of every window` of the new pid, x < main display width).
4. Put the agent Chrome back: `/usr/bin/python3 ~/.hermes/chrome-agent-tools/hermes-chrome-agent hide` on the Mac (`status` shows `mode: hidden`).

## Pitfalls

- Never claim success on a Dock edit before `killall Dock` + a re-read of the plist — the running Dock flushes its in-memory state over the on-disk plist, so pre-restart "success" is meaningless.
- Don't diagnose from the screenshot alone — the `?` icon carries no name. Read the plist to get the actual `_CFURLString`/`file-label`; only then check whether the bundle exists and whether any process matches.
