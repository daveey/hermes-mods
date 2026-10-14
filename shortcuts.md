# Keyboard shortcuts

Every Hermes Desktop shortcut that these mods add or change, where it comes from, and how an agent
can add it to a stock Hermes. On Windows/Linux read ⌘ as Ctrl (`mod` in the keybind registry).
All of them show up in **Settings → Keybinds** and can be rebound there.

## The list

| Shortcut | Does | Source | How you get it |
|---|---|---|---|
| ⌘] / ⌘[ | Next / previous sidebar session that **needs you** (amber or unread dot); wraps. Falls back to the plain neighbour | core patch 0021 | [core/session-navigation-shortcuts.md](core/session-navigation-shortcuts.md) |
| ⌘⇧] / ⌘⇧[ | Next / previous sidebar session, any state (upstream used these for profile next/prev) | core patch 0021 | same |
| ⌘⇧A | Archive the selected (else open) session, then open the next one that needs you | core patches 0019, 0026 | same |
| ⌘R | Send `resume` to the active chat (for a stalled turn) | core patch 0022 | same |
| ⇧⌘R | Reload the window (⌘R used to do this) | core patch 0022 | same |
| ⌘N | New session; returns to the unsent new-session draft (text + attachments) instead of an empty composer | core patch 0036 | [core/persistent-new-session-draft.md](core/persistent-new-session-draft.md) |
| ⌘⇧N | Rename the session; the field is pre-filled with an AI short name | core patch 0020 | [core/ai-session-rename.md](core/ai-session-rename.md) |
| ⌘⇧P | Move the selected session into a project (palette typeahead over every gateway's projects). A name that doesn't exist offers **Create project** with an empty `~/code/<slug>` | core patches 0018, 0030 | [core/project-palette.md](core/project-palette.md) |
| ⇧⌘F | Spin out: turn the follow-up work at the end of a session into new sessions | plugin [spin-out](plugins/spin-out/) | `./install.sh` |
| ⌥⌘F | Session search (moved here from ⇧⌘F by spin-out) | plugin spin-out | `./install.sh` |
| ⌘⇧B | Break the stalled tool call (`/break`) | community plugin intelligent-tool-break | `./install.sh --community` |

The day-to-day loop they're built for: **⌘]** to the next chat waiting on you → answer it →
**⌘⇧A** when it's done (lands on the next waiting chat) → repeat. **⌘R** nudges a stalled turn,
**⌘⇧P** files a chat into a project, **⌘⇧N** gives it a real name.

## Adding them to your Hermes (for agents)

There are two kinds, and they install differently.

### 1. Plugin shortcuts: nothing to build

spin-out and intelligent-tool-break register their shortcuts through the Desktop plugin SDK
(`KEYBINDS_AREA`). Run `./install.sh` (and `--community`). The desktop half hot-reloads; the backend
half needs a gateway or Desktop-backend restart.

### 2. Core shortcuts: patch Hermes, or rewrite as a plugin

⌘] ⌘[ ⌘⇧] ⌘⇧[ ⌘⇧A ⌘R ⌘⇧N ⌘⇧P are changes to Hermes's own source (`apps/desktop/src`). You have three ways:

**a. Apply the patches to a fork (recommended if you'll keep more than one core change).**

```bash
# once: make your hermes-agent checkout a fork
cd ~/.hermes/hermes-agent            # or wherever `hermes` runs from
git remote rename origin upstream    # NousResearch/hermes-agent
git remote add origin git@github.com:<you>/hermes-agent.git
hermes config set --force updates.fork_merge_upstream true   # needs patch 0007 (--force: key isn't in the defaults); see core/fork-updates-and-relaunch.md

# apply the whole series, in order (one conflict, in 0013; see core/session-takeover.md)
git am -3 ~/code/hermes-mods/core/patches/*.patch
git push origin main

# rebuild the Desktop app (macOS: sign it, or Gatekeeper/TCC will treat it as a new app)
APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
  CSC_IDENTITY_AUTO_DISCOVERY=true hermes desktop --build-only --force-build
```

The shortcut patches aren't independent. They share keybind, i18n and sidebar files, so applying a
subset on its own fails. For example, 0019 doesn't apply without the earlier ones, and 0020 doesn't
apply on top of only 0004 + 0018. If you only want some features, apply the series **in order up to
the last patch you need**, then `git revert` what you don't want. Otherwise use option b. See
[core/README.md](core/README.md).

**b. Re-implement on a newer upstream.** If `git am -3` conflicts, hand your agent the feature doc.
Each one has the behavior spec, the code that matters, the tests to port and the gotchas. A prompt
that works:

> Read ~/code/hermes-mods/core/session-navigation-shortcuts.md and the patches it names. Implement
> the same behavior in my hermes-agent checkout against the current code, port the tests, run them,
> rebuild the desktop app, and commit with `[mod]` in the subject.

**c. Rewrite as a Desktop plugin (no fork).** Some of these can work as a plugin, using
`KEYBINDS_AREA` plus the `host` API from `@hermes/plugin-sdk`. Look at
[desktop-plugins/spin-out/plugin.js](desktop-plugins/spin-out/plugin.js) for a working keybind
contribution. Roughly:

| Shortcut | As a plugin? |
|---|---|
| ⌘R resume | Yes, on a different chord: `host.composer.submit(sessionId, 'resume')`. A plugin **can't take ⌘R itself**, because Electron's main process eats ⌘R as "reload" before the renderer sees it. The core patch reroutes that accelerator. |
| ⌘] / ⌘[ | Partly. `host.state` exposes the busy/awaiting atoms, but not the sidebar's drawn order or the unread state. A plugin version would step in recency order (`host.listPersistedSessions`) and use `host.openSession(id)`. |
| ⌘⇧A | Likely: `host.request('session.archive', …)` (a backend method in `tui_gateway/methods_session.py`), then `host.openSession(next)`. Check the params there first. |
| ⌘⇧N | Needs the backend `session.title.suggest` RPC from patch 0020, which you could port into an agent plugin. The rename dialog has to be built in the plugin. |
| ⌘⇧P | Possible through `PALETTE_AREA` + `host.request('session.workspace.move', …)`. The cross-gateway project list is core (0024). |

If a built-in already owns the chord you want, pick another one, or unbind the built-in in
Settings → Keybinds. spin-out does the second for ⇧⌘F.

## Gotchas

- **Rebuild after patching.** Desktop changes don't show up until the app is rebuilt
  (`hermes desktop --build-only --force-build`). A running app keeps the old bundle until it relaunches.
- **Unsigned rebuilds on macOS.** Without `APPLE_SIGNING_IDENTITY` the app is ad-hoc signed, so macOS
  permission grants and launchers that check the signature break. Sign every rebuild the same way.
- **`hermes update` and your commits.** A stock `hermes update` resets the checkout to its `origin`.
  Commit your patches on a fork and turn on `updates.fork_merge_upstream` (patch 0007), or you lose them.
- **Every label needs every locale.** New keybind labels need an entry in every
  `apps/desktop/src/i18n/*.ts` locale and in `overlay-gaps.json`, or the i18n tests fail.
- **Moved bindings.** 0021 takes ⌘⇧]/⌘⇧[ away from profile next/prev. spin-out moves session search
  to ⌥⌘F; reload the window once after installing it.
- **Two shortcuts on one chord.** The registry lets a handler pass through, but two non-passthrough
  actions on one chord is a conflict. The keybind tests in `apps/desktop/src/lib/keybinds/actions.test.ts`
  catch it.
