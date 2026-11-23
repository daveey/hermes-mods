# Desktop: linked running cards merge into the session status dot (mirror 5650–5651)

## Behavior
- A session whose linked cards are running shows the existing working circle; the separate lead square is removed in both sidebar layouts.
- Needs-input and unread precedence is preserved, as is the trailing cluster's exclusion of blue; external-dot precedence holds with running cards.
- Other dot surfaces default to zero linked cards.

## How it works
`session-status-dot.tsx` takes a linked-running-card count and treats it like agent activity; `session-row-card-squares.tsx` drops the lead square; `session-row.tsx` threads the count. The i18n inventory drops unused copy.

## Code
- `apps/desktop/src/app/chat/session-status-dot.tsx`
- `apps/desktop/src/app/chat/sidebar/session-row-card-squares.tsx`
- `apps/desktop/src/app/chat/sidebar/session-row.tsx`
- `apps/desktop/src/i18n/en_session_row_status.ts`

## Tests
Static source evidence (not executed for this documentation audit):

`apps/desktop/src/app/chat/session-status-dot-linked-cards.test.tsx` compares passive-state promotion to the working dot and preserves needs-input/unread. `session-status-dot.test.tsx` checks external/external-down colours with linked work. `sidebar/session-row.test.tsx` checks the sortable grabber dot and absence of the old square; `session-row-card-squares.test.tsx` checks the derived running count. These are component tests, not a native Desktop/browser smoke test (untested).

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files. Regenerate the desktop i18n key inventory (`locales/_keys.desktop.json`).

## Gotchas
- No store or reducer changes.

## plugin?
No. Desktop UI in the core app.
