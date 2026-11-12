# Desktop Kanban status meter and blocked-owner sidebar reason

Patches: **2340–2341, 2404**.

## Behavior

The Kanban status-bar task pill shows a proportional bar of open cards: yellow = not ready (triage/todo/scheduled), green = ready, red = blocked on the user, blue = running; the number is the open total and the tooltip names every segment (four locales). Review-lane cards count as running so a review-only board keeps its pill. Separately, a sidebar session row whose red reason comes from a forwarded blocked owner now names that owner rather than its scheduled relay, and shows no red reason when the remote owner is not blocked.

## How it works

`status-meter.tsx` maps lanes to segments; done/archived are left out. For the sidebar, `stamp_blocked_owners` names blocked owners from the same snapshot that supplies the red count, capped, sorted and unique; the desktop reads them via `session-kanban-label.ts` and the session signature includes them.

## Code

`apps/desktop/src/plugins/kanban/status-meter.tsx`, `plugin.tsx`, `i18n.ts`; `apps/desktop/src/app/chat/sidebar/session-kanban-label.ts`, `session-row.tsx`, `lib/session-signatures.ts`; `hermes_cli/kanban_activity_identity.py`, `kanban_session_activity.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `apps/desktop/src/plugins/kanban/status-meter.test.tsx` (lane mapping, proportional widths, tooltip, review-only conservation); `apps/desktop/src/app/chat/sidebar/session-row.test.tsx`, `src/lib/session-signatures.test.ts`; `tests/hermes_cli/test_kanban_relay_blocked_reason.py` (shared owner, cap, frozen count snapshot, retired reason cleared).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Colour mapping is by lane, so a lane added later is not shown until mapped. The meter is a display of counts, not a scheduling signal.

## Plugin

Part of the bundled desktop Kanban plugin source under `apps/desktop`; no separate plugin install.
