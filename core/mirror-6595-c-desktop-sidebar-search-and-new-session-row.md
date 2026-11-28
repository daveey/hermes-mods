# Desktop sidebar: hidden search and removed New session row

**Scope:** Sidebar chrome: New session row removed, search revealed by shortcut.

**Source ordinals:** 6508, 6509, 6510, 6511, 6512

## Behavior

- The top sidebar 'New session' row, its chevron, shortcut badge and row-specific drag/flash wiring are removed. Creation is retained through the composer and tab plus controls, Cmd+N (session.new), the command palette and the remaining routes; obsolete nav-collapse storage is dropped so a persisted collapsed value cannot strand remaining routes.
- Search starts hidden. The **existing** `session.focusSearch` action (default `mod+shift+f`, unchanged by this series) reveals and focuses the search field: its handler opens the sidebar first, then dispatches the existing focus event, so it works when the sidebar was closed, and repeating it refocuses the same input. Escape clears, hides and stops propagation; blur with an empty or whitespace-only query hides and resets; a non-empty query stays visible and keeps using the existing filtering; Clear dismisses an emptied field. The results/error area stays mounted while a query is active even with no loaded sessions.
- No new keybinding or key handler is added: the search field is only a disclosure of the existing `session.focusSearch` binding (the field listens for the existing focus event rather than registering its own global key handler).
- Tour and creation guidance teach the live session.new binding (and handle unbound controls) and point at the retained composer.
- Electron specs were retargeted: the three transcript/tab fixtures click the retained sidebar `+` (`New session` button), the peer-reset spec drives ordinary same-gateway peer creation with an existing keyboard shortcut (mod+shift+n) and an explicit pinned-profile window with session.new (mod+n), and the other specs press mod+n; the search spec uses mod+shift+f. No binding is added. The `hermes:new-session-shortcut` dispatch is removed from the keybind hook.

## How it works

- Disclosure state lives in `SessionSearchField`; `sidebar/index.tsx` drops the row and nav hook; the 28 full-DOM parity receipt hashes were regenerated for the intentional chrome change.
- The `SESSION_SEARCH_FOCUS_EVENT` dispatched by `requestSessionSearchFocus` is reused; the unused `hermes:new-session-shortcut` event and `use-sidebar-nav-collapsed.ts` (persisted `sidebar.nav.collapsed`) are removed (the hook file is deleted, not modified). The nav-group bottom padding is unconditional because Capabilities is never hidden by sidebar nav preferences.

## Code

Files touched by the series (paths relative to the source tree):

- `apps/desktop/src/app/chat/built-in-tour.ts`
- `apps/desktop/src/app/chat/sidebar/chrome.tsx`
- `apps/desktop/src/app/chat/sidebar/index.tsx`
- `apps/desktop/src/app/chat/sidebar/nav-items.tsx`
- `apps/desktop/src/app/chat/sidebar/presentation-entered-singleton-parity.json`
- `apps/desktop/src/app/chat/sidebar/presentation-singleton-parity.json`
- `apps/desktop/src/app/chat/sidebar/session-search-field.tsx`
- `apps/desktop/src/app/chat/sidebar/sessions-section.tsx`
- `apps/desktop/src/app/chat/sidebar/use-sidebar-nav-collapsed.ts`
- `apps/desktop/src/app/hooks/use-keybinds.ts`
- `apps/desktop/src/lib/tips/catalog.ts`

## Tests

Source test files changed or added by the series:

- `apps/desktop/e2e/correction-session-switch.spec.ts`
- `apps/desktop/e2e/image-attachment-resume.spec.ts`
- `apps/desktop/e2e/large-session-resume.spec.ts`
- `apps/desktop/e2e/peer-device-reset.spec.ts`
- `apps/desktop/e2e/sidebar-background-finish-offscreen.spec.ts`
- `apps/desktop/e2e/sidebar-search-profile-scope.spec.ts`
- `apps/desktop/e2e/sidebar-states.spec.ts`
- `apps/desktop/e2e/unread-dot-restart.spec.ts`
- `apps/desktop/e2e/warm-resume-jitter.spec.ts`
- `apps/desktop/src/app/chat/built-in-tour.test.ts`
- `apps/desktop/src/app/chat/sidebar/chat-sidebar.integration.test.tsx`
- `apps/desktop/src/app/chat/sidebar/session-search-field.test.tsx`
- `apps/desktop/src/app/hooks/use-keybinds-new-session.test.tsx`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6508-6512, 5 commits: 6508, 6509, 6510, 6511, 6512) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Test result caveat: the "107 tests across 9 Desktop files, plus typecheck/build" figure is a **prior owner-reported result only**; it was not reproduced or re-run in this mirror. Electron specs touched here were executed only by the prior owner in isolated windows and failed on backend-readiness and seeded-session/dot preconditions; the source commit text records the pre-feature baseline comparison as still running (the baseline reproduction is supplied by the task context, not by source text), so no attribution is made either way. This doc makes **no all-E2E-green claim**. Desktop E2E is disabled in source CI, so the retargeted Electron specs have no green required-CI evidence. Keyboard dispatch was exercised only in jsdom test windows; installed native renderer behaviour is unverified.
- Snapshot 7018 also includes the navigation-block follow-up described below. The original 6508–6512 behavior and evidence remain historical; in particular, the old unconditional navigation-group padding no longer exists after 7018.
- Removing navigation rows does not unregister their pages or action contributions.
- Nothing in this document is a deployment or live-behaviour claim.

## Navigation-block follow-up (7018)

Source: `6eb51e04afd24f29d8126dd22b29fc28d3060ed6`, ordinal **7018**.

### Behavior and implementation

`ChatSidebar` no longer renders either built-in or contributed page-navigation rows, their context menus, or the group that reserved their space. The content wrapper retains only titlebar clearance. Sessions, Bots, counters, LANES/status/tab surfaces, the session list and existing search disclosure remain. This is presentation removal, not disabling pages, plugins or their registrations: palette navigation, registered actions and shortcuts remain available. No keyboard binding changes; the existing search shortcut and Cmd+N behavior above are unchanged. Obsolete tour stops and tips targeting removed rows are dropped.

### Code and tests

The follow-up changes `sidebar/index.tsx`, `built-in-tour.ts`, `lib/tips/catalog.ts`, both full-DOM parity receipts, `chat-sidebar.integration.test.tsx`, `built-in-tour.test.ts` and `command-palette/hidden-nav-routes.test.tsx` under `apps/desktop/src/app/` (the tips file is under `src/lib/`). Integration expectations cover absence of the navigation block and its spacing while retaining titlebar clearance and session presentation. The palette test exercises four built-in and five contributed routes, including contributed page mounting. Tour expectations cover both interface modes and presets.

Prior implementation evidence reports 571 tests in 30 files and five assertion-killed mutation controls; later post-rebase evidence reports 85 tests in seven files plus typecheck. Those are **prior owner results, not tests rerun by this mirror**. Separately supplied installed-proof evidence reports 12 native checks of the signed package at this exact source stamp, 38 raw CDP responses and no exceptions, without user-window input or restart. That evidence is private and was not rerun here. None of these results establishes all Electron E2E green; the earlier baseline-reproduced failures remain disclosed above. Source inspection is not fresh installation verification.

### Applying and gotchas

Apply the full ordered snapshot through 7018 using the current README recipe and mandatory repair, not this patch alone. Earlier paragraphs describe the original 6508–6512 snapshot; after 7018 the old navigation group and its padding are gone. Route registration is intentionally preserved even though its sidebar entry is absent. This publication performs no build, installation, restart or live UI input.

## Why core, not a plugin?

Desktop renderer.
