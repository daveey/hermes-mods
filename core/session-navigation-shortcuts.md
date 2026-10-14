# Session navigation shortcuts (Desktop)

Keyboard shortcuts for working through the session sidebar: archive a session, jump to the next one that
needs you, step through every session, and nudge a chat with "resume". All of them are entries in the
Desktop keybind registry, so you can rebind them in **Settings → Keybinds**. The defaults are below.

| Patch | Shortcut | Action id | Does |
|---|---|---|---|
| [0019](patches/0019-feat-desktop-A-archives-the-selected-else-open-sessi.patch) | ⌘⇧A | `session.archive` | Archive the selected session, or the open chat if no row is selected |
| [0021](patches/0021-feat-desktop-step-to-the-next-prev-sidebar-session-t.patch), [0043](patches/0043-fix-desktop-step-only-to-sessions-with-a-status-stri.patch), [0050](patches/0050-fix-desktop-and-stop-on-every-row-the-sidebar-paints.patch) | ⌘] / ⌘[ | `session.nextNeedsYou` / `session.prevNeedsYou` | Next / previous sidebar session with a status stripe (green or red), skipping ⌘B-blocked (grey) ones. With none, nothing happens |
| [0048](patches/0048-feat-desktop-B-marks-a-session-blocked-grey-stripe-s.patch) | ⌘B | `session.toggleBlocked` | Mark the selected (else open) session blocked: grey stripe, skipped by ⌘]/⌘[. Again to unblock. ⌘⇧]/⌘⇧[ skip it too (0049). Sidebar toggle moves to ⌥⌘B |
| [0021](patches/0021-feat-desktop-step-to-the-next-prev-sidebar-session-t.patch), [0049](patches/0049-fix-desktop-and-archive-next-fallback-skip-B-blocked.patch) | ⌘⇧] / ⌘⇧[ | `session.nextSession` / `session.prevSession` | Next / previous sidebar session, any state except ⌘B-blocked |
| [0022](patches/0022-feat-desktop-R-sends-resume-to-the-active-chat-R-rel.patch) | ⌥⌘R (⌘R until 0064) | `session.resume` | Send `resume` to the active chat as if you typed it |
| [0022](patches/0022-feat-desktop-R-sends-resume-to-the-active-chat-R-rel.patch) | ⇧⌘R | *(Electron `forceReload`)* | Reload the window (always) |
| [0026](patches/0026-feat-desktop-archive-hotkey-steps-to-the-next-sideba.patch) | ⌘⇧A | `session.archive` | After archiving the open chat, open the next session the same way ⌘] does, not a new draft |

On Windows/Linux, ⌘ is Ctrl (`mod` in the registry).

Typical triage loop: **⌘]** to the next chat waiting on you → read / answer → **⌘⇧A** when it's done (which
lands you on the next waiting chat) → repeat. Use **⌥⌘R** on a chat whose turn stalled.

## Behaviour

### ⌘⇧A: archive (0019, 0026)

- Upstream ships `session.archive` unbound. The fork binds it to `mod+shift+a` ("a" for archive).
- Target: the clicked/selected sidebar row (`$selectedStoredSessionId`), else the chat that's open
  (`$focusedStoredSessionId`). With neither, it does nothing.
- 0026: before archiving, it computes the next session with `nextSidebarSessionId(id, +1, false, true)`, the
  same lookup ⌘] uses (the last arg keeps the plain-neighbour fallback that ⌘] dropped in 0043). The lookup runs while the row is still in the sidebar, so its position is known.
  `archiveSession(id, { onDeselect })` then opens that session in place of upstream's
  `startFreshSessionDraft`. Archiving from the row menu (no `onDeselect`) still drops to a fresh draft.
- An archived session that wasn't the open one leaves the view alone.

### ⌘] / ⌘[ and ⌘⇧] / ⌘⇧[: step through sessions (0021)

- **Order** is the sidebar as drawn, top to bottom: pinned, then recents, including branches. Each rendered
  `SidebarSessionsSection` publishes its visible row ids and a hidden anchor `<span data-sidebar-order-anchor>`
  to `store/sidebar-visible-order.ts`. At read time, sections are sorted by DOM position and deduplicated.
  The list is virtualized, so it can't be read from the DOM; the section row model is the authority. A
  collapsed section contributes nothing. When no section is mounted (sidebar hidden, project view), it falls
  back to the recency list (`$sessions`).
- **"Needs you"** (0043) = the row draws a status stripe ([sidebar-status-stripe.md](sidebar-status-stripe.md)):
  green (finished turn, read or not) or red (blocked on a prompt, or stalled). Eligibility calls the same
  `sessionStatusStripe(dotState, hasTurns)` the row uses, so ⌘]/⌘[ land exactly on striped rows. Since 0050 a
  drawn row publishes its painted stripe (`data-sidebar-stripe`) and that wins: the projects overview draws lane
  rows the flat row model doesn't carry, so green rows there had no `hasTurns` and were skipped (⌘] bounced
  between two rows). For undrawn (virtualized off-screen) rows, `hasTurns`
  comes from the sections' published `turnIds` (rows with `message_count > 0`), else `$sessions`. With no
  striped row they do nothing. (0021 used dot states `needs-input`/`unread` and fell back to the plain
  neighbour; after 0039 kept the green stripe on read sessions, that filter often matched nothing and
  ⌘[ landed on unstriped rows.) ⌘⇧]/⌘⇧[ don't filter.
- **Wrapping:** stepping past either end wraps around. It starts from the selected row, else the open chat.
  With neither, or with a current session that isn't in the sidebar, ⌘] starts at the top and ⌘[ at the bottom.
  It never returns the current session, so it's a no-op when nothing else qualifies.
- **Moved binding:** upstream binds ⌘⇧]/⌘⇧[ to `profile.next`/`profile.prev`. The fork unbinds those, so
  rebind them if you step profiles from the keyboard.

### ⌘B: mark blocked (0048, 0049)

- `session.toggleBlocked` (`mod+b`) flips a per-session flag on the selected row, else the open chat
  (`store/session-blocked.ts`). The flag is a manual park: “waiting on something outside this chat”. Only ⌘B
  clears it; a finishing turn doesn't.
- Stored as a string array in localStorage (`hermes.desktop.blockedSessionIds`), so it is per window profile,
  not synced across devices. Membership is checked across the compression lineage (`lineageAliases`), and
  unblocking removes every alias.
- The row's stripe becomes `blocked` (grey, `bg-zinc-400` / dark `bg-zinc-500`), overriding green/red/none:
  `sessionStatusStripe(state, hasTurns, userBlocked)`.
- `sessionHasStatusStripe` returns false for blocked sessions, so ⌘]/⌘[ skip them. Since 0049
  `nextSidebarSessionId` passes a not-blocked filter to the ⇧ variant and the archive → next fallback too, so
  no stepping shortcut lands on a blocked session.
- **Moved binding:** upstream's sidebar toggle `view.toggleSidebar` moves from ⌘B to ⌥⌘B (`mod+alt+b`).

### ⌘R: resume; ⇧⌘R: reload (0022)

> Since 0064, ⌘R is `app.reloadAll` (restart backend + reload; see [reload-backend-hotkey.md](reload-backend-hotkey.md))
> and `session.resume` defaults to `mod+alt+r`. The ⌘R reroute described below is unchanged and is what 0064 uses.

- `session.resume` (`mod+r`) calls `requestComposerSubmit('resume', { target: 'active' })`, which works like
  typing `resume` and pressing Enter. During a live turn it queues like any other submit. It works while the
  composer has focus (`editableTargetPolicy: 'modified'`).
- Electron's main process grabs ⌘R as the window "reload" accelerator before the renderer sees the keydown.
  The fork intercepts that `reload` command in `use-desktop-integrations.ts`: it looks up whatever is bound
  to `mod+r` in `$comboIndex` and runs it via the new `runBuiltinAction(id)`. The window reloads only if
  nothing is bound or no handler ran. So unbinding `session.resume` restores the upstream ⌘R reload.
- ⇧⌘R (`forceReload`) always reloads the window. No keybind action defaults to `mod+shift+r`, and a test
  enforces that.
- A focused browser preview pane or terminal still gets ⌘R first (`commandFocusedPreview`).

## How it works

All four patches are Desktop renderer only (`apps/desktop/src`). No gateway RPC, no Python, no Electron main
changes. Flow:

- **Registry → handler.** Each shortcut is a `KEYBIND_ACTIONS` entry in `lib/keybinds/actions.ts` (id,
  category, default combos). `app/hooks/use-keybinds.ts` maps ids to handlers and installs that table with
  `registerBuiltinActionRunner`, so a keypress, the command palette and `runBuiltinAction(id)` all hit the same
  handler. `session.archive`'s handler is `archiveSelectedSession`, passed in from `app/contrib/wiring.tsx`.
- **Sidebar order.** `SidebarSessionsSection` publishes `{ anchor, ids }` per section into a module-level
  `Map<symbol, SectionEntry>` (`store/sidebar-visible-order.ts`). `sidebarSessionOrder()` sorts the entries by
  `anchor.compareDocumentPosition`, concatenates and dedupes them. `nextSidebarSessionId()` filters that list
  by `$sessionDotStateById` (`store/session-dot-state.ts`) and steps with `stepSessionId()`. The handler opens
  the result through the existing `goToSession` → `openSession(id, navigate)`.
- **Archive → next.** `archiveSelectedSession` computes `next` first, then calls
  `archiveSession(id, { onDeselect: () => openSession(next, navigate) })`. `archiveSession`
  (`app/session/hooks/use-session-actions/index.ts`) calls `onDeselect` where it used to call
  `startFreshSessionDraft(true)`. That only happens when `wasSelected` (the archived id is the open one).
- **⌘R.** Electron main (`electron/main.ts`, `installPreviewShortcut`) catches ⌘R in `before-input-event`,
  `preventDefault`s it and sends a preview-nav `reload` command to the renderer. Upstream's renderer reloads
  the window on that command. The fork's handler in `use-desktop-integrations.ts` first runs any action bound
  to `mod+r`.

### Files touched

| File | Patches |
|---|---|
| `apps/desktop/src/lib/keybinds/actions.ts` (defaults) | 0019, 0021, 0022 |
| `apps/desktop/src/app/hooks/use-keybinds.ts` (handlers) | 0021, 0022 |
| `apps/desktop/src/store/sidebar-visible-order.ts` (+ test) (new: order registry, `stepSessionId`, `nextSidebarSessionId`) | 0021 |
| `apps/desktop/src/app/chat/sidebar/sessions-section.tsx` (publishes section order) | 0021 |
| `apps/desktop/src/app/contrib/wiring.tsx` (`archiveSelectedSession`) | 0019, 0026 |
| `apps/desktop/src/app/session/hooks/use-session-actions/index.ts` (+ test) (`archiveSession` `onDeselect`) | 0026 |
| `apps/desktop/src/app/contrib/hooks/use-desktop-integrations.ts` (⌘R reroute) | 0022 |
| `apps/desktop/src/lib/keybinds/plugin-actions.ts` (`runBuiltinAction`) | 0022 |
| `apps/desktop/src/i18n/*.ts`, `overlay-gaps.json`, `locales/_keys.desktop.json` (action labels) | 0021, 0022 |


## Code

Essential excerpts from the patches. Locale labels are listed at the end instead of copied.

### `apps/desktop/src/lib/keybinds/actions.ts` (0019, 0021, 0022)

```ts
// 0021: profile steppers lose ⌘⇧] / ⌘⇧[
{ id: 'profile.next', category: 'profiles', defaults: [] },
{ id: 'profile.prev', category: 'profiles', defaults: [] },

// 0021, in the session block after session.focusSearch
{ id: 'session.nextNeedsYou', category: 'session', defaults: ['mod+]'] },
{ id: 'session.prevNeedsYou', category: 'session', defaults: ['mod+['] },
{ id: 'session.nextSession', category: 'session', defaults: ['mod+shift+]'] },
{ id: 'session.prevSession', category: 'session', defaults: ['mod+shift+['] },

// 0022, after session.rename
{ id: 'session.resume', category: 'session', defaults: ['mod+r'], editableTargetPolicy: 'modified' },

// 0019 (was defaults: [])
{ id: 'session.archive', category: 'session', defaults: ['mod+shift+a'] },
```

### `apps/desktop/src/store/sidebar-visible-order.ts` (0021, new file)

```ts
import { $sessions } from '@/store/session'
import { $sessionDotStateById, type SessionDotState } from '@/store/session-dot-state'

interface SectionEntry {
  anchor: HTMLElement | null
  ids: readonly string[]
}

const sections = new Map<symbol, SectionEntry>()

export function publishSidebarSection(key: symbol, entry: SectionEntry): void {
  sections.set(key, entry)
}

export function unpublishSidebarSection(key: symbol): void {
  sections.delete(key)
}

function domOrder(a: SectionEntry, b: SectionEntry): number {
  if (!a.anchor || !b.anchor || a.anchor === b.anchor) {
    return 0
  }

  return a.anchor.compareDocumentPosition(b.anchor) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1
}

export function sidebarSessionOrder(): string[] {
  const ordered = [...sections.values()].filter(entry => entry.anchor?.isConnected).sort(domOrder)
  const seen = new Set<string>()
  const ids: string[] = []

  for (const entry of ordered) {
    for (const id of entry.ids) {
      if (!seen.has(id)) {
        seen.add(id)
        ids.push(id)
      }
    }
  }

  return ids.length ? ids : $sessions.get().map(session => session.id)
}

export const NEEDS_YOU_STATES: ReadonlySet<SessionDotState> = new Set(['needs-input', 'unread'])

export function stepSessionId(
  order: readonly string[],
  currentId: null | string,
  direction: 1 | -1,
  eligible?: (id: string) => boolean
): null | string {
  const n = order.length

  if (n === 0) {
    return null
  }

  const at = currentId ? order.indexOf(currentId) : -1
  const start = at >= 0 ? at : direction === 1 ? -1 : n

  for (let step = 1; step <= n; step += 1) {
    const id = order[(((start + direction * step) % n) + n) % n]

    if (id !== currentId && (!eligible || eligible(id))) {
      return id
    }
  }

  return null
}

export function nextSidebarSessionId(currentId: null | string, direction: 1 | -1, anySession: boolean): null | string {
  const order = sidebarSessionOrder()

  if (!anySession) {
    const dots = $sessionDotStateById.get()
    const needing = stepSessionId(order, currentId, direction, id => NEEDS_YOU_STATES.has(dots[id] ?? 'idle'))

    if (needing) {
      return needing
    }
  }

  return stepSessionId(order, currentId, direction)
}
```

### `apps/desktop/src/app/chat/sidebar/sessions-section.tsx` (0021)

```tsx
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { publishSidebarSection, unpublishSidebarSection } from '@/store/sidebar-visible-order'

// inside SidebarSessionsSection, after visibleRows is computed
const [orderKey] = useState(() => Symbol('sidebar-section'))
const orderAnchorRef = useRef<HTMLSpanElement>(null)

useEffect(() => {
  publishSidebarSection(orderKey, {
    anchor: orderAnchorRef.current,
    ids: sectionOpen ? visibleRows.flatMap(row => (row.kind === 'session' ? [row.entry.session.id] : [])) : []
  })
}, [orderKey, sectionOpen, visibleRows])

useEffect(() => () => unpublishSidebarSection(orderKey), [orderKey])

// first child of the returned <SidebarGroup>
<span aria-hidden className="hidden" data-sidebar-order-anchor="" ref={orderAnchorRef} />
```

### `apps/desktop/src/app/hooks/use-keybinds.ts` (0021, 0022)

```ts
import { nextSidebarSessionId } from '@/store/sidebar-visible-order'
import { requestComposerSubmit /* , … */ } from '../chat/composer/focus'

const currentSessionForStep = () => $selectedStoredSessionId.get() || $focusedStoredSessionId.get()

// in the handler map (goToSession already exists upstream: id → openSession(id, navigate))
'session.nextNeedsYou': () => goToSession(nextSidebarSessionId(currentSessionForStep(), 1, false)),
'session.prevNeedsYou': () => goToSession(nextSidebarSessionId(currentSessionForStep(), -1, false)),
'session.nextSession': () => goToSession(nextSidebarSessionId(currentSessionForStep(), 1, true)),
'session.prevSession': () => goToSession(nextSidebarSessionId(currentSessionForStep(), -1, true)),
'session.resume': () => requestComposerSubmit('resume', { target: 'active' }),
```

### `apps/desktop/src/app/contrib/wiring.tsx` (0019, 0026)

```tsx
import { $focusedStoredSessionId } from '@/store/session-focus'
import { nextSidebarSessionId } from '@/store/sidebar-visible-order'

const archiveSelectedSession = useCallback(() => {
  // The clicked row, else the chat that's open.
  const sessionId = $selectedStoredSessionId.get() || $focusedStoredSessionId.get()

  if (!sessionId) {
    return
  }

  // Compute BEFORE the row leaves the sidebar so its position is still known.
  const next = nextSidebarSessionId(sessionId, 1, false)
  const onDeselect = next && next !== sessionId ? () => openSession(next, navigate) : undefined

  void archiveSession(sessionId, { onDeselect })
}, [archiveSession, navigate])
```

### `apps/desktop/src/app/session/hooks/use-session-actions/index.ts` (0026)

```ts
const archiveSession = useCallback(
  async (storedSessionId: string, options?: { onDeselect?: () => void }) => {
    // … unchanged optimistic removal; wasSelected = selectedStoredSessionIdRef.current === storedSessionId
    if (wasSelected) {
      if (options?.onDeselect) {
        options.onDeselect()
      } else {
        startFreshSessionDraft(true)
      }
    }
    // … setSessionArchived RPC, rollback on failure
```

### `apps/desktop/src/lib/keybinds/plugin-actions.ts` (0022)

```ts
/** Run a built-in action's handler by id (no plugin allowlist — app-internal
 *  callers only). False when the keybind shell isn't mounted or has no handler. */
export function runBuiltinAction(id: string): boolean {
  return runner ? runner(id) : false
}
```

### `apps/desktop/src/app/contrib/hooks/use-desktop-integrations.ts` (0022)

```ts
import { canonicalizeCombo } from '@/lib/keybinds/combo'
import { runBuiltinAction } from '@/lib/keybinds/plugin-actions'
import { $comboIndex } from '@/store/keybinds'

// in the preview-nav command listener
if (!commandFocusedPreview(command) && command === 'reload') {
  const bound = $comboIndex.get().get(canonicalizeCombo('mod+r')) ?? []

  if (bound.some(id => runBuiltinAction(id))) {
    return
  }

  window.location.reload()
}
```

### i18n

Labels go under `keybinds.actions.<id>`:

- 0021: `session.nextNeedsYou`, `session.prevNeedsYou`, `session.nextSession`, `session.prevSession`.
- 0022: `session.resume` (en: `Send "resume" to current chat`).

They're added to `en.ts`, `de.ts`, `es.ts`, `fr.ts`, `ru.ts`, `zh.ts` and `ar_chrome.ts`. `ja` and `zh-hant` are
recorded as gaps in `i18n/overlay-gaps.json`, and the keys are added to `locales/_keys.desktop.json`.
`session.archive` already had labels upstream.

## Tests

```sh
cd apps/desktop
npx vitest run src/store/sidebar-visible-order.test.ts \
  src/lib/keybinds/actions.test.ts \
  src/app/session/hooks/use-session-actions.test.tsx
```

- `sidebar-visible-order.test.ts`: wrapping, skipping ineligible sessions, null when nothing qualifies,
  starting at the ends for an unknown current id, DOM-ordered and deduplicated section concat, and dropping
  disconnected anchors.
- `actions.test.ts`: `session.archive` defaults to `mod+shift+a`, `session.resume` to `mod+r` (allowed in
  the composer), and nothing claims `mod+shift+r`.
- `use-session-actions.test.tsx`: archiving the open session calls `onDeselect` without navigating. Without
  it, the session still drops to a fresh draft.

## Applying

| Patch | Needs |
|---|---|
| 0019 | nothing beyond upstream |
| 0021 | 0019, 0020 (⌘⇧N rename: its `session.rename` lines are the hunk context in `actions.ts`, `use-keybinds.ts` and the i18n files) |
| 0022 | 0021 (i18n and `use-keybinds.ts` context) |
| 0026 | 0019 (`$focusedStoredSessionId` import), 0021 (`sidebar-visible-order.ts`). 0023 edits the same `archiveSession` function just below, so apply it first |
| 0043 | 0021, 0026, 0038 (`sessionStatusStripe`), 0039 |
| 0050 | 0043, 0048 |

Apply the whole series in numeric order from the hermes-agent repo root:

```sh
cd ~/.hermes/hermes-agent            # your checkout
git am -3 /path/to/hermes-mods/core/patches/00{19,20,21,22,23}-*.patch
git am -3 /path/to/hermes-mods/core/patches/0026-*.patch
```

If you skip 0020, expect `git am -3` conflicts in 0021. Resolve them by keeping both sides (drop the
`session.rename` entries), or re-implement 0021 from the **Code** section. Nothing here touches Python or the
gateway RPC contract, so there's nothing to regenerate.

## Gotchas

- **Rebuild the desktop app** after applying. The patches are renderer code:
  `hermes desktop --build-only --force-build`. On macOS, set `APPLE_SIGNING_IDENTITY` (or `CSC_LINK`) first,
  or the build is ad-hoc signed.
- **Lint:** 0022 adds imports to `use-desktop-integrations.ts` in an order that `perfectionist/sort-imports`
  rejects (`@/lib/hermes-open-target` must come before `@/lib/keybinds/plugin-actions`). Run
  `npx eslint --fix src/app/contrib/hooks/use-desktop-integrations.ts` in `apps/desktop`. Patch 0033 is
  exactly that fix, so apply it after 0022.
- **i18n:** every new action id needs a label in `en.ts` (the `Translations` type is the contract) and either a
  translation or an `overlay-gaps.json` entry for each other locale. `src/i18n/overlay-completeness.test.ts`
  fails on a new untranslated key without a gap entry. After changing `en.ts`, regenerate
  `locales/_keys.desktop.json` with `npm run i18n:keys` in `apps/desktop` and commit it (`--check` verifies).
- **Keybind registry rules** (`actions.ts`): `actions.test.ts` requires unique ids and an `en` label for every
  action. Two actions with the same default both map to that combo in `$comboIndex`, so 0021 empties
  `profile.next`/`profile.prev` to make ⌘⇧] / ⌘⇧[ unambiguous. A combo that should fire while the composer
  has focus needs `editableTargetPolicy`; `session.resume` uses `'modified'`.
- **⌘R never reaches the renderer as a keydown.** Electron main's `installPreviewShortcut` (`electron/main.ts`)
  `preventDefault`s ⌘R and sends the preview-nav `reload` command. That's why 0022 hooks the command handler,
  not just the registry. The default isn't enough: rebinding `session.resume` to another combo works through
  the normal keydown path, but binding some other action to `mod+r` only works because of the `$comboIndex`
  lookup.
- **`runBuiltinAction` skips the plugin allowlist** (`PLUGIN_APP_ACTIONS`). It's app-internal. Don't expose it
  through the SDK.
- **Stale comment:** after 0019, the upstream comment above `session.archive` still says it "ships unbound". The
  code is right; the comment isn't.
- **Updating:** these are local commits on your checkout, so re-apply or rebase them whenever you pull
  upstream. Upstream changes `actions.ts` and the i18n files often, so their hunk context is the likeliest to
  conflict.

## Troubleshooting

- **⌘] does nothing:** no other session is visible. Check that the sections are expanded and the sidebar
  filter isn't hiding rows.
- **⌘] skips a chat you expected:** it has no green/red stripe (working, background, draft, or no messages).
- **⌘R reloads the window:** `session.resume` is unbound or rebound, or the keybind shell wasn't mounted yet
  (`runBuiltinAction` returned false). Check Settings → Keybinds.
- **⌘⇧] switches profiles:** the 0021 defaults change isn't in this build, or `profile.next` was rebound by hand.

## Could this be a plugin instead?

Partly. The desktop plugin SDK (`apps/desktop/src/sdk/index.ts`, imported as `@hermes/plugin-sdk`) can
contribute rebindable keybinds via `KEYBINDS_AREA` (`{ id, label, category?, defaults?, run }`, see
`desktop-plugins/spin-out/plugin.js`). Built-in ids can't be shadowed, and a plugin can't change a built-in's
defaults.

| Feature | As a plugin? |
|---|---|
| ⌘R resume | **No for ⌘R.** `host.composer.submit(sessionId, 'resume')` is enough for the action, but Electron main eats ⌘R before the renderer's keydown listener, and only the core `reload` command handler can reroute it. A plugin can ship the same action on another combo (e.g. `mod+alt+r`). |
| ⌘] / ⌘[ stepping | **Approximately.** `host.state` exposes `focusedStoredSessionId` and `activeSessionId`, but not the sidebar's selected row, the per-session dot states (`$sessionDotStateById`), or the visible sidebar order. A plugin could rebuild "needs you" from `host.request(method, params)` session reads plus `busyBySession`, then open the result with `host.openSession(id)`. The order and the unread/needs-input states would drift from what the sidebar draws. `mod+]`/`mod+[` are free upstream. `mod+shift+]`/`[` collide with `profile.next`/`prev`, which a plugin can't unbind for the user. |
| ⌘⇧A archive | **Archive yes, "go to next" no.** `session.archive` is a built-in with no default and isn't in `PLUGIN_APP_ACTIONS`, so `ctx.runAction` can't call it. The user can bind ⌘⇧A to it in Settings → Keybinds with no code. A plugin action would have to archive via `host.request`/REST itself and wouldn't get the optimistic sidebar update or the `onDeselect` hand-off in `archiveSession`. `host.sessions` only exposes pin, reorder and colour. |

Bottom line: the fork is needed for ⌘R and for exact sidebar-order, dot-state-aware stepping. A plugin can get
close for the rest on different combos.
