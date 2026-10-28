# Sidebar 'created' sort stops reshuffling on activity

Patches:

- `patches/0158-fix-desktop-created-sidebar-sort-stops-reshuffling-o.patch`: the created sort key drives the base sort, branch-cluster order and the date buckets from `started_at`.
- `patches/0571-fix-desktop-created-sidebar-sort-keeps-projects-in-p.patch`: under the created sort, projects stop moving too.
- `patches/0572-fix-desktop-sidebar-sort-key-ranks-each-lane-row-by-.patch`: lanes, gateway groups and overview previews rank each row by its own fields, not by an id order of the loaded sessions page.

What you see: with the sidebar sorted by Created, a reply in an old chat no longer lifts it to the top or into the newest date bucket.

## Behavior

- Before: under date grouping the created sort only re-slotted rows inside date buckets cut by `last_active`, so activity in an old chat moved it into the newest bucket.
- Now the created key (`started_at`) decides the base sort, branch-cluster (root/sibling) order and the date buckets. The default sort still uses last activity.
- Projects (0571): under the created sort the project list no longer hoists the active project or re-sorts by last activity. Order is Home, explicit projects, then auto repos, each by label, with a hand-dragged order layered on top; projects new to that order append below it instead of jumping above it. The default sort keeps the activity order and active-project hoist.
- Lane rows (0572): project lanes, gateway groups and the project overview previews rank each row by the sort key read from the row itself (`$sidebarSessionRank`). Before, they ranked by `$sidebarSessionRankIds`, an id order built from the loaded `$sessions` page; rows outside it (backend preview rows, other gateways' rows) were folded in by their slot in the recency-sorted pool, so under the created sort they moved whenever activity reordered the lane. Ties keep arrival order. The flat list still uses the id order (all its rows are loaded).

## How it works

- `lib/session-branch-tree.ts`: new `SessionTime` type, `sessionActivityAt` (`last_active || started_at`) and `sessionCreatedAt` (`started_at`); `FlattenSessionsOptions.timeOf` picks the clock for roots and sibling branches.
- `lib/session-date-groups.ts`: `groupEntriesByRecency(..., timeOf)` and `headRunCutoffMs` bucket by that clock.
- `app/chat/sidebar/index.tsx` and `sessions-section.tsx` pass `sessionCreatedAt` when the sort key is created.
- `app/chat/sidebar/projects/model.ts`: `sortProjectsForOverview(..., { stable })` and `orderProjectsByIds(..., { stable })`; `index.tsx` passes `stable: ordering === 'created'`.
- `store/sidebar-sort.ts`: `$sidebarSessionRank` (row → rank, null when unranked); `$sidebarSessionRankIds` is derived from it. `order.ts` `rankSessions(rows, rank)` sorts by the key; `workspace-group.tsx`, `gateway-groups.tsx` and `overlayLivePreviews` (`rank` option) use it.

## Code

`apps/desktop/src/lib/session-branch-tree.ts`, `session-date-groups.ts`, `app/chat/sidebar/index.tsx`, `sessions-section.tsx`.

## Tests

- `lib/session-date-groups.test.ts` ('groupEntriesByRecency with the created clock'): an old chat replied to an hour ago buckets by `started_at`, with a control for the default clock.
- `app/chat/sidebar/projects/model.test.ts` ('stable project order (created sort)'): no active hoist or activity order, same order after activity changes, explicit above auto with Home on top, default sort still activity-led, a fresh active project appends below the drag order.
- `app/chat/sidebar/order.test.ts` ('rankSessions'): rows rank by their own key whatever order the lane arrives in; ties keep arrival order.
- `app/chat/sidebar/projects/workspace-groups.test.ts` ('keeps created order when activity lands on a preview row the sessions page never loaded').
- `store/sidebar-sort.test.ts` ('$sidebarSessionRank'): null on the default sort; ranks rows that are not in `$sessions`.
- Untested: `workspace-group.tsx` and `gateway-groups.tsx` wiring (component paths), beyond tsc.
- `app/chat/sidebar/sessions-section-created-sort.test.tsx`: under the created sort rows order by `started_at`, a row does not move when an older session gets activity, and the default 'updated' sort still orders by activity.

## Applying

Desktop-only. Standalone, but touches the sidebar files other patches touch, so apply in order.

## Gotchas

- A session's displayed age label still reads last activity; only ordering and dividers follow creation.

## Plugin?

No. The sort lives in core sidebar rendering.
