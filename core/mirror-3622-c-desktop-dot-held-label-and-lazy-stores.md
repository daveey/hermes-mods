# Desktop sidebar dot: held cards keep "running", owner-route changes repaint, store list built lazily

Patches: **3606–3607**.

Follows [3526](mirror-3529-c-desktop-sidebar-waiting-dot.md), which introduced the "waiting" label and the live-store subscription of the sidebar background dot.

## Behavior
- A held card (a todo card behind a human ask) no longer counts as known-waiting work. A row such as `{kanban_active: 2, kanban_held: 1, kanban_parked: 1}` keeps the `running` label, matching the red (blocked) stripe that `kanbanStripeCounts` paints for the same row. Only parked cards and current waiting observations make a row "waiting".
- The dot repaints at once, not on the next 15 s tick, when the owner route changes: a tile naming the owner of a session, an active-route switch, an activation-epoch change, or a change in the cron, messaging, project-tree or unlisted-owner rows.
- Still tick-only (module state without a store): the per-runtime event owner map, remembered owner hints, and the gateway registry's primary/secondary sockets.
- The list of stores the dot reads is built on first use instead of at module import, so test suites that partially mock the session or gateway stores and never render a dot no longer fail to load.

## How it works
- 3606 changes `rowOnlyWaiting` in `session-dot-state.ts`: `parked` is now `row.kanban_parked ?? 0` (it no longer adds `kanban_held`), and a nonzero `kanban_held` disqualifies the row from "waiting". In `execution-observation-row.ts` it adds a computed `$tileOwnerBindings` key (per tile: stored session id, runtime id, owner profile, owner route; layout-only tile edits leave it unchanged) and extends the store list with `$tileOwnerBindings`, `$activeGatewayRoute`, `$gatewayActivationEpoch`, `$cronSessions`, `$messagingSessions`, `$projectTree` and `$unlistedSessionOwnerRows`. `DOT_ROW_STORES` in `use-session-dot-state.ts` drops its now-duplicate `$messagingSessions`.
- 3607 replaces the module-level `ROW_EXECUTION_STORES` constant with a memoized `rowExecutionStores()` function, and the hook's `DOT_ROW_STORES` constant with a memoized `dotRowStores()`. The same singleton array still reaches `useStoresSelector` on every render. The reason recorded: reading `$unlistedSessionOwnerRows` / `$activeGatewayRoute` at import threw under a vitest partial mock in three suites that never render a dot.

## Code
- `apps/desktop/src/store/session-dot-state.ts`
- `apps/desktop/src/store/execution-observation-row.ts`
- `apps/desktop/src/lib/use-session-dot-state.ts`
- `apps/desktop/src/store/session-dot-state.test.ts`
- `apps/desktop/src/app/chat/session-status-dot.test.tsx`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/src/store/session-dot-state.test.ts`: `keeps the running label while a held card paints the stripe red` (three rows with a held card, each with a blocked stripe count and a `running` label); the earlier all-parked "waiting" case no longer includes a held row.
- `apps/desktop/src/app/chat/session-status-dot.test.tsx`: `keeps running when a held card sits beside the waiting one`; `relabels as soon as a tile names the owner whose roster reports a running card` (setting `$sessionTiles` repaints without a tick change).
- Untested: repaint on `$activeGatewayRoute`, `$gatewayActivationEpoch`, cron/messaging/project-tree/unlisted-owner changes individually; 3607 has no test file of its own (the lazy list is only exercised by the suites above, and the partial-mock load failure is described in the commit message, not in a patch test).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- 3607 depends on 3606's expanded store list; apply both together.
- Owner changes that only touch module state with no store still wait up to one 15 s tick.
