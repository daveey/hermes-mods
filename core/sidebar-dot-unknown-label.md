# Sidebar dot: "status unknown" label for unknown-only background work

Patches: **3469**. Complements [execution-observations.md](execution-observations.md) and [sidebar-status-stripe.md](sidebar-status-stripe.md).

## Behavior

- A sidebar row's `background` dot whose claim rests only on unknown or unfresh work evidence now has the aria-label and title **"Open work, current status unknown"** (i18n key `sidebar.row.backgroundUnknown`) instead of "Background task running". Unknown or unfresh evidence means a failed or stale Kanban read (`kanban_unknown`, `remote_stale`), an absent or expired observation, or an `unknown` execution observation, with no process, subagent, goal or current running/queued execution for the row.
- The dot's claim and color are unchanged: unknown work still keeps the row unfinished (unknown must never read as done). Only the words change.
- A row with a running card, a live process (renderer-polled or backend `background_active`), async subagents or a goal keeps "Background task running".
- Rows held only by known waiting or blocked work keep the existing label.

## How it works

- `backgroundDotUnknown(row, liveElsewhere, nowSeconds)` in `apps/desktop/src/store/session-dot-state.ts` returns true when nothing positively runs (no process, subagent, goal, or current running/queued execution from the existing `summarizeExecutionObservations` counts) and the claim rests on unknown or unfresh evidence. `liveElsewhere` is the store's own running evidence for the row.
- `useBackgroundDotUnknown(id, row, state)` in `apps/desktop/src/lib/use-session-dot-state.ts` computes it only for a `background` dot, checking every lineage alias of the session against `$backgroundRunningSessionIds` and `$delegatingSessionIds`, and re-evaluates on the existing `$kanbanObservationTick` (time-only expiry). It adds no store or poll.
- `sessionDotVariant(state, outcome, backgroundUnknown)` in `apps/desktop/src/app/chat/session-status-dot.tsx` swaps the background variant's aria-label/title to the new key; the session row passes the hook's result.
- The key is added to the English session-row strings and its type, translated in ar, de, es, fr, ja, ru, zh and zh-hant, and recorded in `locales/_keys.desktop.json`.

## Code

- `apps/desktop/src/store/session-dot-state.ts` (`backgroundDotUnknown`)
- `apps/desktop/src/lib/use-session-dot-state.ts` (`useBackgroundDotUnknown`)
- `apps/desktop/src/app/chat/session-status-dot.tsx` (`sessionDotVariant(..., backgroundUnknown)`)
- `apps/desktop/src/i18n/en_session_row_status.ts`, `apps/desktop/src/i18n/types_session_row_status.ts`, and the `ar_chrome`, `de`, `es`, `fr`, `ja`, `ru`, `zh`, `zh-hant_chrome` locale files
- `locales/_keys.desktop.json`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patch; they describe coverage, not fresh pass results. Source-commit RED/GREEN or mutation reports are not mirror-run evidence.

- `apps/desktop/src/store/session-dot-state.test.ts`: `is unknown when the claim rests only on unknown or unfresh evidence`, `says running when a process, subagent, goal or running card exists`, `keeps the existing label for known waiting or blocked work` (predicate).
- `apps/desktop/src/app/chat/sidebar/session-row.test.tsx`: `labels a row whose only open work is unknown as unknown, not running`, `keeps the running label when this window polls a live process for the unknown-only row`, `keeps the running label for a row with a running card`, `keeps the running label when a live process holds a row with unfresh card evidence`, plus an expired-evidence row case (rendered Desktop row label).

Untested: the non-English translations beyond type-checking, and a live Desktop against a real hub.

## Applying

Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas

- Rows held only by known waiting work still carry the running label; giving them their own wording is a separate follow-up.
- The hook's `state === 'background'` short-circuit is a performance guard only: `sessionDotVariant` ignores the flag for every other state.
