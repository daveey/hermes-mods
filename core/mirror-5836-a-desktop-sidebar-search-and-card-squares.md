# Desktop sidebar: title-first search, error alert, card colour squares (mirror 5450, 5520)

## Behavior
- Server session search (`hermes_cli/web_routers/sessions.py`) now orders ID, then title, then FTS5 message-content hits (the message-search block moved after the title lane; its docstring and a comment say title matches precede content hits).
- A full-store search failure shows a localized retry alert even when loaded-page matches exist (new i18n key across locales).
- Flat (one-line) session rows replace the trailing task counts and process badge with up to six distinct card-colour squares (6 px, 2 px gap, one per colour in urgency order) placed before the gateway mark. The colours are red (needs you, else blocked), pink (held), amber (stalled + attention), blue (running: active minus parked and held), grey (waiting: parked minus stalled, unknown execution, unknown kanban) and green (unseen results); the label and top tooltip use the same text with counts. Card-mode rows keep their age and badges (per the tests' names). The age, tokens and cost options are removed from the Show filter menu; a test asserts their sort keys and legacy persistence are kept.

## How it works
`use-server-session-search.ts` and `search-states.tsx` implement search/alert; `session-row-card-squares.tsx` renders the squares inside `session-row.tsx`; `filter-menu.tsx` loses the options; new strings live in `en_session_row_status.ts`, `overlay-gaps.json` and `locales/_keys.desktop.json`.

## Code
`apps/desktop/src/app/chat/sidebar/` (index.tsx, search-states.tsx, use-server-session-search.ts, session-row.tsx, session-row-card-squares.tsx, filter-menu.tsx), `apps/desktop/src/i18n/*`, `hermes_cli/web_routers/sessions.py`, `locales/_keys.desktop.json`.

## Tests
Source: `use-server-session-search.test.tsx` (failures exposed and retried for the same query/profile; retry alert shown alongside loaded matches and cleared after retry; failure cleared when the query clears; stale rejection after a profile change ignored), `session-row-card-squares.test.tsx` (colour derivation rules, urgency order, one square per colour, nothing without card fields, accessible label equals tooltip), `session-row.test.tsx` (flat trailing slot ignores legacy age/token/cost preferences; card-mode age and badges unchanged; process badge hidden on flat rows; count hidden on one-line row, kept on card), `filter-menu.test.tsx`; backend `tests/hermes_cli/test_web_server_session_search_titles.py` (title precedes crowded content hits; title lane precedes content hits). Not executed in this mirror pass.

## Applying
Independent patches; both touch the sidebar directory.

## Gotchas
The six `cardColours` strings exist only in English and the eight other locales are listed in `overlay-gaps.json`; the retry-alert string is translated in the locale files touched.

## plugin?
No (Desktop app code).
