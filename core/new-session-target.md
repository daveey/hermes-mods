# New sessions on a saved gateway (device switcher "New sessions on ▸")

Patches **0813** (`066b48864d`), **0816** (`0c7f598467`).

## Behavior
- The status-bar device switcher keeps its menu (gateways, Manage gateways…) and gains one row above Manage:
  **New sessions on: <gateway> ▸**, a submenu listing the gateways as radio choices. Picking one saves it (profile
  `default`) as the app-wide new-session route. It does not switch what is on screen. The saved gateway's row in the
  main list shows a small home mark ("New sessions start here"). 0813 first shipped this as a per-row right-click;
  0816 moved it into the menu because the right-click replaced the row's existing context menu.
- The choice is sticky: Electron persists it (`active-profile.json` → `defaultRoute`), so it survives a relaunch.
  Opening or clicking a session on another gateway (sidebar, Inbox, search, deep link) never changes it.
- Generic new sessions use it: the sidebar **New session** row (upstream), `/new` (upstream), the tab-strip "+" /
  Open in split (upstream), **⌘N**, the command palette's **New session**, and the shell right-click **New session**.
  ⌘N, the palette row and the shell item all run `session.new`.
- Still explicit, not the saved gateway: **⌘⇧N** (gateway on screen), a gateway group's or project's "+",
  "New session in <gateway>".
- With no saved gateway, ⌘N keeps patch 0042's This-device behaviour and the other paths follow the gateway on
  screen (upstream).
- Saved gateway unreachable: the draft stays bound to it (nothing is created anywhere else). A warning toast
  "<gateway> isn't reachable" carries the dial error and one action, **Create on This device instead**, which starts
  this one draft on `local`/`default` and leaves the saved gateway alone. No action when the saved gateway is This
  device.
- The upstream profile-row "Set as default" writes the same route, so it can pick a non-default profile.
- Upstream side effect of a saved route, unchanged: a cold launch with no open session lands on that gateway.

## How it works
Reuses upstream's `$defaultProfileRoute` (`store/default-profile.ts`, Electron `desktop-profile.ts`), which
`defaultNewSessionTarget()` / `prepareDefaultNewSession()` (`app/session/new-session-route.ts`) already apply to
generic new sessions. The fork adds the switcher entry point, routes ⌘N/palette/shell through it, and replaces the
generic "Failed to open profile" toast for a routed target with `notifyNewSessionTargetUnreachable`.

## Code
- `apps/desktop/src/app/chat/sidebar/connection-switcher.tsx` — `DropdownMenuSub` "New sessions on", home mark
- `apps/desktop/src/app/hooks/use-keybinds.ts` — `session.new` uses the saved route when there is one
- `apps/desktop/src/app/session/new-session-route.ts` — `notifyNewSessionTargetUnreachable`
- `apps/desktop/src/app/command-palette/index.tsx`, `apps/desktop/src/app/context-menu/shell-menu-items.tsx` —
  `runBuiltinAction('session.new')`, navigation fallback
- i18n: `profiles.newSessionsOn` … `profiles.createOnThisDevice` (en; other locales in `overlay-gaps.json`)

## Tests
- Device menu keeps its rows, the New sessions on submenu saves without switching, trigger names the target, row is
  marked: `src/app/chat/sidebar/connection-switcher.test.tsx`
- Sidebar row after opening a session on another gateway still creates on the saved gateway; no-saved-gateway control
  follows the gateway on screen; unreachable notice + This-device action; no action for a local target:
  `src/app/session/hooks/new-session-target.test.tsx`
- ⌘N uses the saved gateway, ⌘⇧N does not: `src/app/hooks/use-keybinds-new-session.test.tsx`
- Command palette New session runs `session.new`: `src/app/command-palette/new-session-target.test.tsx`
- Shell right-click New session runs `session.new`: `src/app/context-menu/app-context-menu.test.tsx`
- `/new` and tab "+" with a saved route: upstream `src/app/session/hooks/default-new-session.test.tsx`
- Relaunch persistence: `electron/desktop-profile.test.ts`
- Untested: the Inbox/search/deep-link open paths individually (they share the resume path the sidebar test drives).

## Applying
After 0042. `git am -3 --empty=keep core/patches/0813-*.patch core/patches/0816-*.patch`.

## Gotchas
- The route's profile is `default` when picked from the switcher; use the profile row's "Set as default" for another.
- The switcher only renders with two or more gateways.

## Plugin?
No: the switcher, keybind handler and new-session routing are core renderer code.
