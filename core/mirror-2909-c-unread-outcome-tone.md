# Unread session dots use the stored outcome tone

Patches: **2855, 2860**.

## Behavior

An unread session row keeps its filled/new-content dot, but waiting/running/unresolved/decision/muted outcomes use their stripe’s tone rather than success emerald. The label becomes “Unread — <outcome>”. Done, missing and unknown outcomes retain the existing emerald fallback.

## How it works

- **2855:** `unreadDotOutcomeStripe` and `unreadDotOutcomeLabel` map stored outcomes; `sessionDotVariant` consults them only for `unread`. Status ordering, unread counts and non-unread states do not change. Translation copies and key types gain `unreadNotDone`.
- **2860:** add the rendered muted-row regression and explain why profile rollups and canonical bot rows still use emerald: they lack one qualified session outcome to paint.

## Code

- `apps/desktop/src/app/chat/session-status-dot.tsx`
- `apps/desktop/src/i18n/ar_chrome.ts`
- `apps/desktop/src/i18n/de.ts`
- `apps/desktop/src/i18n/en_session_row_status.ts`
- `apps/desktop/src/i18n/es.ts`
- `apps/desktop/src/i18n/fr.ts`
- `apps/desktop/src/i18n/ja.ts`
- `apps/desktop/src/i18n/ru.ts`
- `apps/desktop/src/i18n/types_session_row_status.ts`
- `apps/desktop/src/i18n/zh-hant_chrome.ts`
- `apps/desktop/src/i18n/zh.ts`
- `apps/desktop/src/lib/session-outcome.ts`
- `locales/_keys.desktop.json`
- `apps/desktop/src/app/chat/sidebar/profile-switcher.tsx`
- `apps/desktop/src/plugins/hermes-bots/bot-row.tsx`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`session-outcome.test.ts` checks all outcome mappings, labels and fallback kinds. `session-row.test.tsx` renders parked unread rows for non-done outcomes, done/no outcome controls, and a muted/blocked-tone case.

- `apps/desktop/src/app/chat/sidebar/session-row.test.tsx`
- `apps/desktop/src/lib/session-outcome.test.ts`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

This is session-row presentation, not a declaration that unread means done. Bot/profile aggregate dots intentionally keep their prior behavior. Live Desktop rendering and translations are not exercised by this mirror.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
