# Desktop: external-run cards paint sky/red with service hover and explicit expiry clocks

Mirror entries 5179-5184.

## Behavior

- The session stripe gains `external` (sky) and `external_down` (red) colors with a cloud glyph, hollow when the state is unknown. Precedence: user-blocked first, then live turn, then stuck/red (including external down), external, stalled, waiting, done. A scheduled card with external work reads "running externally"; down reads red. Hover names the service and status; the sidebar lists up to 5 external cards then "+N more". Absent fields keep the previous output.
- 5180: a stale next-due time no longer paints red while the hover says "ok"; a capped card list cannot override count stamps (the max of derived and stamped counts is used, so a down card outside the listed ones still paints red); the hollow cloud follows client-derived unknown state; down-only dots are labelled down.
- 5181: the unknown cloud keeps its inherited sky stroke (only the fill is transparent) and the kanban meter hover includes external and external-down counts in all locale formatters.
- 5182: render evidence expires on explicit observation clocks (the mounted row keeps the same task object and the compiler caches implicit `Date.now`), a plugin-local ticking clock drives badge and meter, and missed checks read "overdue".
- 5183 is test-only (observation tick independent of the timer fixture). 5184 keeps the source-authority slot when external stripe callers build their arguments, so automated rows resurface when external work is down but not when merely running.

## How it works

- `apps/desktop/src/lib/external-run.ts`, `lib/session-status-stripe.ts`, `store/session-dot-state.ts`, `store/sidebar-automated.ts`, `sidebar-visible-order.ts`.
- `app/chat/session-status-dot.tsx`, `sidebar/session-row-stripe*.ts(x)`, `session-tab-accent.tsx`.
- Kanban plugin UI: `external-run.ts`, `external-run-ui.tsx`, `use-external-run-clock.ts`, `status-meter.tsx`, `board.tsx`, `drawer.tsx`, `plugin.tsx`.
- Locale files for ar, de, es, fr, ja, ru, zh, zh-hant and `locales/_keys.desktop.json`.

## Code

- `apps/desktop/src/lib/external-run.ts`
- `apps/desktop/src/lib/session-status-stripe.ts`
- `apps/desktop/src/app/chat/sidebar/session-row-stripe.tsx`
- `apps/desktop/src/app/chat/session-status-dot.tsx`
- `apps/desktop/src/plugins/kanban/external-run-ui.tsx`
- `apps/desktop/src/plugins/kanban/use-external-run-clock.ts`
- `apps/desktop/src/store/session-dot-state.ts`

## Tests

Source tests (not executed in this mirror pass; vitest): `lib/external-run.test.ts`, `lib/session-status-stripe.test.ts`, `store/session-dot-state.test.ts`, `store/sidebar-automated.test.ts`, `session-status-dot.test.tsx`, `session-row-stripe.test.tsx`, `session-row-stripe-external.test.ts`, `plugins/kanban/external-run-ui.test.tsx`, `external-run-meter-clock.test.tsx`, `status-meter.test.tsx`. Cases include stale-derived down hover, hollow cloud computed stroke/fill, mounted stripe/dot/badge/meter expiry without a board update, capped list versus stamps. Real browser rendering and the non-English locale wording are untested.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- The red/sky state depends on the backend external-state fields (see the external-run card state doc); without them nothing changes.
- Locale keys must stay in sync with `_keys.desktop.json`.

## Why core instead of a plugin

The stripe, dot and sidebar are the Desktop app's built-in session chrome with their own i18n key catalog.
