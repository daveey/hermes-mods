# Done chat names the cards it still watches

Patches: **3502**.

## Behavior
- A chat whose outcome is done, but which has a card parked with `watch=true`, now reports those cards as `watches` instead of looking plainly finished.
- The sidebar row stripe stays green, draws an eye glyph, and the tooltip reads "Still watching: <card> (wakes <time>)"; crons and watches are named together (Desktop; covered by component and lib tests).
- A plain done chat draws no glyph; a waiting outcome with watches is left alone.
- The outcome audit reports `done_watching` (untested). The kanban schedule tool's watch description and result tell the agent to name the pending check (wording untested).
- A watch mark clears once the card is done, and a plain scheduled card counts as a wait, not a watch.

## How it works
3502 makes the outcome computation attach the chat's watch-parked cards (like owned crons) via new helpers in `hermes_cli/kanban_watch.py`, extends the outcome types and `session-outcome.ts`, renders the glyph and tooltip in `session-row-stripe.tsx` with new i18n strings, and adds `done_watching` to the outcome audit.

## Code
- `hermes_cli/kanban_watch.py`
- `hermes_cli/sessions_cmd_outcome.py`
- `hermes_state_outcome.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`
- `apps/desktop/src/app/chat/sidebar/session-row-stripe.tsx`
- `apps/desktop/src/lib/session-outcome.ts`
- `apps/desktop/src/types/session-outcome.ts`
- `apps/desktop/src/i18n/en_session_row_status.ts`
- `apps/desktop/src/i18n/types_session_row_status.ts`
- `apps/desktop/src/i18n/overlay-gaps.json`
- `locales/_keys.desktop.json`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_kanban_watch_park.py`: `test_done_chat_names_its_watch_park`, `test_watch_mark_clears_once_the_card_is_done`, `test_plain_scheduled_card_is_a_wait_not_a_watch`.
- `apps/desktop/src/app/chat/sidebar/session-row-stripe.test.tsx`: 'a done chat that still watches a card draws the eye glyph and names the check', 'a plain done chat draws no watch glyph'.
- `apps/desktop/src/lib/session-outcome.test.ts`: 'stays green and names the pending check with its wake time', 'names crons and watches together', 'leaves a plain done, or a waiting outcome with watches, alone'.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
