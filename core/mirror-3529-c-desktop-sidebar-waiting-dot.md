# Desktop sidebar background dot: live stores, listed-row fallback, waiting label

Patches: **3526**.

## Behavior
- The sidebar background dot updates as soon as the live work roster, background status, subagents, session states or sessions change, instead of lagging up to the next 15 s tick.
- A dot with no caller-supplied row (e.g. a contributor tab lead, unresolved stored row) falls back to the listed session or messaging-session row by id, so a store-claimed background or goal activity is no longer shown as "status unknown".
- A fresh row held only by known waiting work (all cards parked/held or current waiting observations; nothing unparked, blocked, needing the user, or with an outstanding ask) reads "Open work, waiting". The label is localised in 8 locales.

## How it works
- 3526 switches `useBackgroundDotUnknown` / `useSessionDotState` to `useStoresSelector` over `ROW_EXECUTION_STORES`; `$sessionStates` is reduced to a computed runtime-to-stored-id key so per-token republishes do not re-run the selector. `backgroundDotUnknown` becomes `backgroundDotLabel` returning `running | unknown | waiting`. New key `sidebar.row.backgroundWaiting` is added to the English strings, the type, 7 other locale files and `locales/_keys.desktop.json`.

## Code
- `apps/desktop/src/app/chat/session-status-dot.tsx`
- `apps/desktop/src/lib/use-session-dot-state.ts`
- `apps/desktop/src/store/session-dot-state.ts`
- `apps/desktop/src/store/execution-observation-row.ts`
- `apps/desktop/src/i18n/*.ts` (ar, de, en, es, fr, ja, ru, zh-hant, zh, types)
- `locales/_keys.desktop.json`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/src/app/chat/session-status-dot.test.tsx`: roster-running relabel without tick; idle tree row repaint; listed-row read for store-claimed activity; waiting for a fresh waiting card; waiting for all-parked listed row; running beside a waiting card.
- `apps/desktop/src/store/session-dot-state.test.ts`: `backgroundDotLabel` cases (waiting for known wait; running label kept for blocked or unparked work).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Blocked, unparked, needs-you or outstanding-ask work never reads "waiting".
