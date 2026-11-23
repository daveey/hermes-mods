# Desktop: sessions.filter plugin contribution area (mirror 5658–5661)

## Behavior
- Desktop plugins can contribute `sessions.filter` entries; all contributions are ANDed. A match that is not a function or throws counts as matching (warned once per id).
- The sidebar 'filters on' state reflects plugin filters; reset calls each contribution's `onClear`.
- Core and navigation predicates stay independent of plugin contributions so list-dependent registration cannot feed back into its own SDK snapshot; order is core, plugin, navigation.

## How it works
`store/session-filter.ts` holds `applySessionFilters` and `sessionHasOpenCards`; `sidebar/session-filters.ts` applies it after status/profile/PR checks and before project navigation. The SDK exports `SESSION_FILTER_AREA`, `SessionFilterContribution`, `sessionHasOpenCards`, `useSidebarSessions`. Paging settles plugin filter clearing before restoring paging.

## Code
- `apps/desktop/src/store/session-filter.ts`
- `apps/desktop/src/app/chat/sidebar/session-filters.ts`
- `apps/desktop/src/app/chat/sidebar/use-session-filters.ts`
- `apps/desktop/src/app/session/hooks/use-session-list-actions.ts`
- `apps/desktop/src/sdk/index.ts`

## Tests
Static source evidence (not executed for this documentation audit):

`apps/desktop/src/store/session-filter.test.ts` checks AND composition, fail-open warnings, open-card fields and per-contribution clear callbacks. `app/chat/sidebar/chat-sidebar.integration.test.tsx` checks visible rows/reset and list-dependent registration stability. `app/session/hooks/session-filter-paging.test.ts` checks deepening, same-tick replacement and settled disposal. Native plugin installation/reload and a live Desktop smoke test are untested by these component/hook cases.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Existing filters, the filter menu and card colours are unchanged.

## plugin?
Core Desktop SDK extension point; its consumers are Desktop plugins.
