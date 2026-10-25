# Runners Desktop — Observe

Native SDK-only ESM status-bar contribution, order 6. Existing account-usage and core-update chrome is unchanged. Requires the Desktop SDK's `host.profileRoutes()`, connection-aware `ctx.rest`, React Query, Popover, ContextMenu and StatusDot. Missing routing fails visibly; there is no focused-gateway fallback.

## Behavior

The enabled chip always renders. Counts are registrations, not machine counts or available slots. The narrow form retains full accessible counts and tooltip. The upward end-aligned panel is viewport-bounded (680px, 70vh), groups explicitly mapped hosts, and places all other registrations in “Host not mapped”. Capacity is unknown unless enforced, and declared capacity stays advisory.

All connected profiles are the default. Requests pin `connectionId` and explicit backend `?profile=`. Responses establish a stable gateway UUID/profile cache identity; changed owners and regressed epochs fail closed. A newer epoch for the same owner may refresh inventory, but captured actions retain their old fence. Removed or unavailable routes retain stale last observations. Different owners claiming the same provider registration create an explicit conflict and disable actions. Same-owner duplicates select reachable/newest evidence deterministically.

Search covers names, host, scope, OS, architecture and labels. Filters: All, Needs attention, Busy, Ready, Maintenance. Host warnings are counted once, not once per registration. Scope failures stay at source level. The basic v1 runner contract has no offline-confirmation count: the UI says “Offline observation”, never inventing a confirmed outage. Busy without job evidence says “Busy — job not identified”.

Inventory polling is 15s expanded / 60s collapsed, paused when the document is hidden. Opening requests one deduplicated asynchronous collection refresh. There are no per-row inventory requests; bounded details load only on expansion. Inventory fanout is limited to four requests concurrently. The collector remains gateway-owned and independent of the UI.

Keyboard: palette “Show runners”, `/` focuses search, arrows move registrations, Enter toggles inline details, Shift+F10 opens the same native actions as right-click/overflow. Escape dismisses menu, then details, then popover; focus returns to the appropriate row/chip. No global shortcut is added. Theme tokens, native focus rings and reduced-motion overrides are used.

Refresh and validated GitHub settings links are supported. Diagnostic copy uses the SDK clipboard door and a bounded field allowlist (no raw logs/config). Repair menu placeholders are hidden behind the `repair_sessions` component feature flag (default false); even when exposed they are disabled. Maintenance controls are disabled and explicitly state that maintenance does not drain jobs. This UI does not create sessions, restart runners, dispatch jobs or modify maintenance.

## Tests

From the repository root, Node >=22:

```sh
npm ci --prefix desktop-plugins/runners/tests --ignore-scripts --no-audit --no-fund
node --test desktop-plugins/runners/tests/*.test.mjs
```

The harness uses real React, React Query and Radix Popover/ContextMenu with jsdom, isolated host atoms and fake REST. It never accesses live credentials or dispatches a job. Fixtures originate from `plugins/runners/contracts/fixtures.json`. Tests cover freshness/empty/error states, explicit routing and owner races, removed owners, duplicate registrations, host warnings, long names, 100 rows/no request amplification, menus, keyboard focus, hidden polling and palette registration. Tests cannot establish native screenshot fidelity or deployed backend availability; those require real Desktop integration evidence.

Normal installation: the repository installer or hermes-mods sync links this directory under the Desktop plugin discovery root. Disable/remove the Desktop contribution to roll back UI only; no runner services are affected.
