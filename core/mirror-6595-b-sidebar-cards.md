# Background owner chats, card counts and elapsed clocks

Patch coverage: 6231, 6296, 6297, 6313, 6317, 6318, 6319.

## Behavior

Open-card producer owner chats appear in a collapsible Background section rather than ordinary Pinned/Projects presentation. Background rows and header show numeric counts by card colour, while normal pinned marks retain their prior presentation.

Sidebar rows can display per-card running/blocked elapsed clocks derived from backend activity and advanced locally without extra backend polling. Offline retained rows suppress these clocks. Owner-qualified card-set navigation can show an exact board/card selection without retargeting it when foreground gateway changes.

## How it works

The owner policy recognizes the durable owner prefix, including compressed lineage roots, keeps backend pins in the identity exclusion set and sorts open owners by activity. Collapse state persists independently. Backend timer projection derives current-attempt start or live ask timing with lineage/dedup/limit handling; renderer orders clocks, caps width/count and keeps full tooltip details.

sidebar.sections.top adds a contribution mount above Pinned. __hermesKanbanShowCards snapshots immutable connection/board/card identities and navigates to the board view. Replacement selection closes the old drawer; ordinary task/new-card navigation clears the filter. Polls use owner-qualified passive board reads and drawer status columns come only from the selected board.

## Code

Affected implementation and integration paths in the source series:

- `apps/desktop/src/app/chat/sidebar/background-section.tsx`
- `apps/desktop/src/app/chat/sidebar/card-counts-context.ts`
- `apps/desktop/src/app/chat/sidebar/contributed-sections.tsx`
- `apps/desktop/src/app/chat/sidebar/index.tsx`
- `apps/desktop/src/app/chat/sidebar/owner-chats.ts`
- `apps/desktop/src/app/chat/sidebar/session-row-card-squares.tsx`
- `apps/desktop/src/app/chat/sidebar/session-row-card-timers.tsx`
- `apps/desktop/src/app/chat/sidebar/session-row.tsx`
- `apps/desktop/src/app/chat/sidebar/use-wait-clock.ts`
- `apps/desktop/src/app/routes.ts`
- `apps/desktop/src/i18n/ar_chrome.ts`
- `apps/desktop/src/i18n/de.ts`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/es.ts`
- `apps/desktop/src/i18n/fr.ts`
- `apps/desktop/src/i18n/ja.ts`
- `apps/desktop/src/i18n/ru.ts`
- `apps/desktop/src/i18n/types.ts`
- `apps/desktop/src/i18n/zh-hant_chrome.ts`
- `apps/desktop/src/i18n/zh.ts`
- `apps/desktop/src/plugins/kanban/card-set.tsx`
- `apps/desktop/src/plugins/kanban/i18n.ts`
- `apps/desktop/src/plugins/kanban/plugin.tsx`
- `apps/desktop/src/sdk/index.ts`
- `apps/desktop/src/types/hermes-session-activity.ts`
- `hermes_cli/fact_annotation_kanban.py`
- `hermes_cli/kanban_activity_identity.py`
- `hermes_cli/kanban_activity_projection.py`
- `hermes_cli/kanban_annotation_facts.py`
- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_activity_timers.py`
- `hermes_cli/sessions_projection_manifest.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `apps/desktop/src/app/chat/sidebar/owner-chats.test.ts`
- `apps/desktop/src/app/chat/sidebar/background-section.test.tsx`
- `apps/desktop/src/app/chat/sidebar/session-row-card-timers.test.tsx`
- `tests/hermes_cli/test_kanban_session_activity_timers.py`
- `apps/desktop/src/plugins/kanban/card-set.test.tsx`
- `apps/desktop/src/plugins/kanban/card-set-navigation.test.tsx`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas
The card-set hook in this range snapshots connection identity, reads the board passively with `profile: null`, and stamps the drawer owner with `profile: 'default'`; it is not a general arbitrary-profile selector. Backend owner qualification must not be confused with permission to retarget a selection.

Backend pins still protect idle chats even if their Background row is omitted. Elapsed display is not a new authoritative execution clock; offline rows are historical. Invalid card-set input must not destroy a valid selection, and a stale disposer must not remove a replacement hook.

## Why core, not a plugin?

The sidebar host owns pin partitioning, session activity projection and owner-routed navigation; plugins can contribute through the new slots/hooks but cannot safely alter host row identity.
