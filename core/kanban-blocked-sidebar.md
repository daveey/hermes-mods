# Blocked tasks in the sidebar, quieter Kanban notifications

Patch **0105**.

## Behavior

The left sidebar has a collapsible **Blocked** section between Pinned and Projects. It lists blocked cards across all boards and gateways, ordered by priority and then newest creation time. It shows the total count and initially five rows; load-more adds ten. Clicking a row opens that task's drawer on its own board. Hover shows its title and latest summary. The section disappears when nothing is blocked, and its open state persists in plugin storage.

Kanban `completed` events no longer produce either in-app toasts or OS notifications. `blocked` events no longer toast: their persistent sidebar rows replace those popups, while the away-from-Hermes OS notification remains. `crashed`, `gave_up`, `timed_out` and `block_loop_detected` still toast.

## How it works

Core introduces the `sidebar.sections` render-contribution area. `SidebarContributedSections` mounts registered contributions between the existing sidebar sections with contribution error boundaries. The SDK exports matching `SidebarSection` and `SidebarSectionRow` components so plugin-owned lists use the same row geometry and collapse/load-more controls.

The bundled Kanban plugin registers `BlockedTasksSection`. `fetchBlockedTasks` reads `/boards` per gateway and requests `/board?board=<slug>` only for boards whose counts report blocked cards. Older responses without counts fall back to reading each board. Results carry board/gateway ownership, populate the task-owner map for drawer routing, and are sorted by priority then creation time. A failed gateway or board does not discard successful results; all gateway-level reads failing rejects the query.

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

## Tests

From a patched checkout:

```bash
cd apps/desktop
npx vitest run src/app/chat/sidebar/contributed-sections.test.tsx src/plugins/kanban/api.blocked.test.ts src/plugins/kanban/completion-notify.test.ts
```

The contribution tests cover empty registration, mounting/disposal, row clicks, load-more and collapse. API tests cover count-based board selection, priority ordering, board/gateway ownership, a failed gateway and total gateway failure. The notification suite's `quiet kinds` block checks fully silent completion events, cursor advancement, continued crash toasts, and blocked events that retain OS delivery without a toast.

These are component/API unit tests, not a live multi-gateway end-to-end test.

## Applying

Apply the ordered series **0001–0105** with `git am -3` from the fork/upstream merge-base in a throwaway worktree; see [README.md](README.md). Patches share sidebar, SDK and Kanban files, so 0105 alone is not a verified independent apply set. Rebuild Desktop to load the UI changes and enable the bundled Kanban plugin.

## Gotchas

- This is blocked Kanban cards, not the manually parked session flag toggled by ⌘B.
- Sidebar search hides contributed sections. Empty/loading results also render no section.
- Failed gateways/boards can yield a partial list; the count is the fetched result count, not proof every gateway answered.
- Other boards and gateways may take a polling interval to refresh. Open/collapsed state is persisted; the expanded row limit is component-local.
- The notification helper's default remains unchanged when no quiet policy is supplied. The bundled plugin opts in explicitly.
- This replaces the completion/blocked toast behavior documented for patches 0058/0062 in [kanban-toast-status-icon.md](kanban-toast-status-icon.md).

## Could this be a plugin?

The Kanban list, fetching and notification policy are in the bundled plugin. The new sidebar render area, its mounting point and SDK component exports require core support; an external plugin cannot supply that missing host area by itself.
