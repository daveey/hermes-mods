# Plugins can take over the ⌘⇧A archive-and-advance hotkey

Patch: `0147`. Used by the public `desktop-plugins/inbox` plugin.

## Behavior

⌘⇧A (`session.archive`, see [session-navigation-shortcuts.md](session-navigation-shortcuts.md)) archives the
selected (else open) session and steps to the next sidebar session that needs you. A plugin can now claim the
hotkey for sessions it owns. With the Inbox plugin, ⌘⇧A on an Inbox message archives it and opens the **next Inbox
message** (the one below it, else the top of what's left). Only on the last message does it fall back to core's
step to the next sidebar session. If the message was selected but not open, it is archived without navigating.
Sessions no plugin claims behave exactly as before.

## How it works

`store/archive-hotkey.ts` keeps a registry of handlers. `archiveSelectedSession` (wiring) computes core's next
sidebar session as before, then calls `dispatchArchiveHotkey`: each handler gets
`{ sessionId, isFocused, openNextSession }` in registration order, and the first to return `true` owns the
hotkey (core neither archives nor navigates). `openNextSession()` runs core's own next-session step, so a handler
can archive its last item and hand navigation back. A handler that throws is skipped (console warning). If none
claims it, `archiveSession(id, { onDeselect })` runs as before, including the linked-work confirm (0110).

Plugins register with `ctx.registerArchiveHotkeyHandler(handler)`; the registration is removed on unload.
Why a takeover rather than just "pick the next id": Inbox messages are hidden from the sidebar and may live on
another gateway or profile, and `archiveSession` routes unlisted sessions to the active gateway only. The Inbox
plugin archives through its own owner-aware `POST /items/{sid}/archive` and opens rows with an owner route.

## Code

- `apps/desktop/src/store/archive-hotkey.ts`: registry, `runArchiveHotkeyHandlers`, `dispatchArchiveHotkey`
- `apps/desktop/src/app/contrib/wiring.tsx`: `archiveSelectedSession` dispatches through it
- `apps/desktop/src/contrib/plugin.ts`: `PluginContext.registerArchiveHotkeyHandler`
- Plugin side: `desktop-plugins/inbox/plugin.js` (`nextItemAfter`, `store.archiveHotkey`)

## Tests

Core, from `apps/desktop`: `npx vitest run src/store/archive-hotkey.test.ts` (5 tests): unclaimed sessions go to
core `archive` with core's `onDeselect`; a claiming handler replaces core and receives the id, focus flag and a
working `openNextSession`; `openNextSession` is a no-op with no next session; a throwing handler is skipped and
order is kept; the plugin-context registration is removed on unload. `tsc --noEmit`, eslint, and
`src/store/archive-guards.test.ts` + `src/contrib` (91 tests total) pass.

Plugin: `/opt/homebrew/bin/node --test desktop-plugins/inbox/test/plugin.test.mjs` (15 tests): next-item choice,
archive via the owning gateway then open the next message, last message → core `openNextSession`, selected but
not open → no navigation, non-inbox session → not claimed, register works with and without the core hook.

Untested: the real keypress end to end in a running Desktop (the wiring call itself is covered only by tsc).

## Applying

Apply on top of the fork series; it touches only the three renderer files above plus the new store module.

## Gotchas

- Needs a rebuilt Desktop: until the core update lands, the plugin feature-detects
  (`ctx.registerArchiveHotkeyHandler?.`) and ⌘⇧A on an Inbox message falls back to core's archive.
- Handlers are synchronous: the keypress decides immediately, so a plugin must answer from state it already has.

## Plugin?

The hotkey dispatch lives in core's keybind wiring (a window capture listener registered before any plugin), so a
plugin cannot intercept ⌘⇧A first; the hook is the minimal core seam. The Inbox behavior itself is plugin code.
