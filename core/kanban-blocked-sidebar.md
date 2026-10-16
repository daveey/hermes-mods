# Blocked tasks in the sidebar, quieter Kanban notifications

Patches **0105** and **0107** (grouping by managing chat).

## Behavior

The left sidebar has a collapsible **Blocked** section between Pinned and Projects. It lists blocked cards across all boards and gateways, ordered by priority and then newest creation time. It shows the total count and initially five rows; load-more adds ten. Clicking a row opens that task's drawer on its own board. Hover shows its title and latest summary. The section disappears when nothing is blocked, and its open state persists in plugin storage.

Since 0107 the cards are grouped under the chat managing them. Each group has a header row showing the session title (or "Untitled session") and its card count, with the cards indented below. Clicking the header opens that chat when it lives on the local gateway; on another gateway it opens the group's first card instead. Cards no chat owns sit last under "No session". A group sits where its most urgent card would rank. The five-row cap counts cards, not headers.

Kanban `completed` events no longer produce either in-app toasts or OS notifications. `blocked` events no longer toast: their persistent sidebar rows replace those popups, while the away-from-Hermes OS notification remains. `crashed`, `gave_up`, `timed_out` and `block_loop_detected` still toast.

## How it works

Core introduces the `sidebar.sections` render-contribution area. `SidebarContributedSections` mounts registered contributions between the existing sidebar sections with contribution error boundaries. The SDK exports matching `SidebarSection` and `SidebarSectionRow` components so plugin-owned lists use the same row geometry and collapse/load-more controls.

The bundled Kanban plugin registers `BlockedTasksSection`. `fetchBlockedTasks` reads `/boards` per gateway and requests `/board?board=<slug>` only for boards whose counts report blocked cards. Older responses without counts fall back to reading each board. Results carry board/gateway ownership, populate the task-owner map for drawer routing, and are sorted by priority then creation time. A failed gateway or board does not discard successful results; all gateway-level reads failing rejects the query.

**Managing chat (0107).** `GET /board` stamps each card with `managing_session_id`: its most recent `tui` notify subscriber (`kanban_notify_subs`, ordered by `created_at`), since a chat asked to shepherd someone else's card subscribes to it, else the creating `session_id`. After collecting a gateway's blocked cards, `fetchBlockedTasks` calls the new `GET /api/plugins/kanban/session-labels?ids=` on that gateway once. It returns each id's live compression tip (`SessionDB.get_compression_tip`) and title, so cards subscribed under pre-compression ids land in one group. A backend without the endpoint falls back to grouping by the raw id, untitled. `groupBlockedTasks` in `blocked-section.tsx` does the grouping, keyed by gateway and session.

The `blockedKey(scope)` query polls every 30 seconds. Live event frames also invalidate it; polling covers boards without a socket and other gateways. `blockedSectionOpen` is restored from `ctx.storage` (default true) and updated through an atom listener.

`plugin.tsx` passes `quiet: { silent: ['completed'], toastless: ['blocked'] }` through `bindApi` into `completion-notify.ts`. Both quiet policies still advance the event cursor. `silent` bypasses delivery altogether; `toastless` skips only the in-app notification. The native shell retains its existing away-only OS delivery rule.

## Code

Paths relative to `apps/desktop/src/`:

- `app/routes.ts`: `SIDEBAR_SECTIONS_AREA`.
- `app/chat/sidebar/contributed-sections.tsx`: contribution renderer and shared section/row components.
- `app/chat/sidebar/index.tsx`: mounts contributions when sidebar search is empty.
- `sdk/index.ts`: area and component/type exports.
- `plugins/kanban/blocked-section.tsx`: five-row initial cap, ten-row increments, 30-second query and task navigation.
- `plugins/kanban/api.ts`: `fetchBlockedTasks`, `blockedKey`, ownership and event invalidation; quiet-policy forwarding.
- `plugins/kanban/plugin.tsx`: contribution registration, persisted open state and notification policy.
- `plugins/kanban/completion-notify.ts`: `CompletionNotifyQuiet`, cursor-safe suppression.
- `plugins/kanban/i18n.ts`: section labels and hover text.
- `plugins/kanban/dashboard/plugin_api.py` (repo root, 0107): `_managing_sessions`, `managing_session_id` on board cards, `GET /session-labels`.
- `plugins/kanban/types.ts` (0107): `session_id`, `managing_session_id`.

## Tests

From a patched checkout:

```bash
cd apps/desktop
npx vitest run src/app/chat/sidebar/contributed-sections.test.tsx src/plugins/kanban/api.blocked.test.ts src/plugins/kanban/completion-notify.test.ts
```

The contribution tests cover empty registration, mounting/disposal, row clicks, load-more and collapse. API tests cover count-based board selection, priority ordering, board/gateway ownership, a failed gateway and total gateway failure. The notification suite's `quiet kinds` block checks fully silent completion events, cursor advancement, continued crash toasts, and blocked events that retain OS delivery without a toast.

0107 adds `src/plugins/kanban/blocked-section.test.ts` (group order, sessionless last, per-gateway keys), two `api.blocked.test.ts` cases (tip/title resolution, fallback without the endpoint), and `tests/plugins/test_kanban_managing_session.py` (latest tui subscriber beats the creator, CLI cards have none, labels follow compression; run with `scripts/run_tests.sh`).

These are component/API unit tests, not a live multi-gateway end-to-end test.

## Applying

Apply the ordered series **0001–0107** with `git am -3` from the fork/upstream merge-base in a throwaway worktree; see [README.md](README.md). Patches share sidebar, SDK and Kanban files, so 0105/0107 alone are not a verified independent apply set. Rebuild Desktop to load the UI changes and enable the bundled Kanban plugin.

## Gotchas

- Opening a group's chat works for the local gateway only. The SDK's `openSession` route needs a full connection descriptor the plugin doesn't have for remote gateways.
- The manager is the latest subscriber, not necessarily the chat you last talked to about the card.
- This is blocked Kanban cards, not the manually parked session flag toggled by ⌘B.
- Sidebar search hides contributed sections. Empty/loading results also render no section.
- Failed gateways/boards can yield a partial list; the count is the fetched result count, not proof every gateway answered.
- Other boards and gateways may take a polling interval to refresh. Open/collapsed state is persisted; the expanded row limit is component-local.
- The notification helper's default remains unchanged when no quiet policy is supplied. The bundled plugin opts in explicitly.
- This replaces the completion/blocked toast behavior documented for patches 0058/0062 in [kanban-toast-status-icon.md](kanban-toast-status-icon.md).

## Could this be a plugin?

The Kanban list, fetching and notification policy are in the bundled plugin. The new sidebar render area, its mounting point and SDK component exports require core support; an external plugin cannot supply that missing host area by itself.
