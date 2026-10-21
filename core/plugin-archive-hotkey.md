# Plugins can take over the ⌘⇧A archive-and-advance hotkey

Patches: `0147`, `0157` (`ctx.openNextSession`). Used by the public `desktop-plugins/inbox` plugin.

## Behavior

⌘⇧A (`session.archive`, see [session-navigation-shortcuts.md](session-navigation-shortcuts.md)) archives the
selected (else open) session and steps to the next sidebar session that needs you. A plugin can now claim the
hotkey for sessions it owns. With the Inbox plugin, ⌘⇧A on an Inbox message archives it and opens the **next Inbox
message** (the one below it, else the top of what's left). Only on the last message does it fall back to core's
step to the next sidebar session. If the message was selected but not open, it is archived without navigating.
Sessions no plugin claims behave exactly as before.

The same step is reachable outside the hotkey (0157): `ctx.openNextSession(fromId)` opens the session ⌘⇧A would
step to from `fromId` (next status-striped sidebar row, else the plain neighbour; for an id hidden from the sidebar,
such as an Inbox message, that is the first eligible row from the top, as with ⌘⇧A) and returns false when there is
none. The Inbox uses it when a card's own button (Archive, a `resolve` or `task` action, also via its ⌥1…⌥9 keys)
closes the **last** open message, so the chat no longer stays on the now-archived session.

## How it works

`store/archive-hotkey.ts` keeps a registry of handlers. `archiveSelectedSession` (wiring) computes core's next
sidebar session as before, then calls `dispatchArchiveHotkey`: each handler gets
`{ sessionId, isFocused, openNextSession }` in registration order, and the first to return `true` owns the
hotkey (core neither archives nor navigates). `openNextSession()` runs core's own next-session step, so a handler
can archive its last item and hand navigation back. A handler that throws is skipped (console warning). If none
claims it, `archiveSession(id, { onDeselect })` runs as before, including the linked-work confirm (0110).

Plugins register with `ctx.registerArchiveHotkeyHandler(handler)`; the registration is removed on unload.

`ctx.openNextSession` (0157): wiring installs one opener with `registerNextSessionOpener(nextSessionOpenerFor(pick,
open))`, where `pick` is the same `nextSidebarSessionId(id, 1, false, true)` the hotkey uses and `open` is
`openSession(next, navigate)`. `openNextSessionFrom(id)` calls it; false before the shell mounts, for an empty id,
when the pick is none or `fromId` itself, or when the opener throws. A stale disposer never removes a newer opener.
Why a takeover rather than just "pick the next id": Inbox messages are hidden from the sidebar and may live on
another gateway or profile, and `archiveSession` routes unlisted sessions to the active gateway only. The Inbox
plugin archives through its own owner-aware `POST /items/{sid}/archive` and opens rows with an owner route.

## Code

- `apps/desktop/src/store/archive-hotkey.ts`: registry, `runArchiveHotkeyHandlers`, `dispatchArchiveHotkey`;
  0157 adds `registerNextSessionOpener`, `nextSessionOpenerFor`, `openNextSessionFrom`
- `apps/desktop/src/app/contrib/wiring.tsx`: `archiveSelectedSession` dispatches through it; 0157 installs the opener
- `apps/desktop/src/contrib/plugin.ts`: `PluginContext.registerArchiveHotkeyHandler`, 0157 `openNextSession`
- Plugin side: `desktop-plugins/inbox/plugin.js` (`nextItemAfter`, `store.archiveHotkey`, `leave`)

## Tests

Core, from `apps/desktop`: `npx vitest run src/store/archive-hotkey.test.ts` (5 tests): unclaimed sessions go to
core `archive` with core's `onDeselect`; a claiming handler replaces core and receives the id, focus flag and a
working `openNextSession`; `openNextSession` is a no-op with no next session; a throwing handler is skipped and
order is kept; the plugin-context registration is removed on unload. 0157 adds 5 more (10 total):
`openNextSessionFrom` is false before an opener exists and after its disposer runs, and opens core's pick with the
source id; the opener never re-opens the source and reports no next session; a throwing opener returns false; a
stale disposer keeps a newer opener; `ctx.openNextSession` from a real plugin context reaches the opener. `tsc --noEmit`, eslint, and
`src/store/archive-hotkey.test.ts` + `src/store/archive-guards.test.ts` + `src/contrib` (96 tests total) pass.

Plugin: `/opt/homebrew/bin/node --test desktop-plugins/inbox/test/plugin.test.mjs` (50 tests in the file): next-item choice,
archive via the owning gateway then open the next message, last message → core `openNextSession`, selected but
not open → no navigation, non-inbox session → not claimed, register works with and without the core hook. For
0157: last card closed by resolve → `ctx.openNextSession(id)`; by task/archive with no next session or on an older
core (no `openNextSession`) → `ctx.runAction('session.new')`; a card that isn't the open chat never navigates.

Untested: the real keypress end to end in a running Desktop, and the wiring's opener install (covered only by tsc;
the opener's logic is the tested `nextSessionOpenerFor`).

## Applying

Apply on top of the fork series; it touches only the three renderer files above plus the new store module.

## Gotchas

- Needs a rebuilt Desktop: until the core update lands, the plugin feature-detects
  (`ctx.registerArchiveHotkeyHandler?.`) and ⌘⇧A on an Inbox message falls back to core's archive.
- `ctx.openNextSession` is fork-core only (0157): until the update lands and Desktop reloads, the Inbox falls back to
  opening a fresh draft after closing the last card.
- Handlers are synchronous: the keypress decides immediately, so a plugin must answer from state it already has.

## Plugin?

The hotkey dispatch lives in core's keybind wiring (a window capture listener registered before any plugin), so a
plugin cannot intercept ⌘⇧A first; the hook is the minimal core seam. The Inbox behavior itself is plugin code.
