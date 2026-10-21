# Sidebar 'created' sort stops reshuffling on activity

Patches:

- `patches/0158-fix-desktop-created-sidebar-sort-stops-reshuffling-o.patch`: the created sort key drives the base sort, branch-cluster order and the date buckets from `started_at`.

What you see: with the sidebar sorted by Created, a reply in an old chat no longer lifts it to the top or into the newest date bucket.

## Behavior

- Before: under date grouping the created sort only re-slotted rows inside date buckets cut by `last_active`, so activity in an old chat moved it into the newest bucket.
- Now the created key (`started_at`) decides the base sort, branch-cluster (root/sibling) order and the date buckets. The default sort still uses last activity.

## How it works

- `lib/session-branch-tree.ts`: new `SessionTime` type, `sessionActivityAt` (`last_active || started_at`) and `sessionCreatedAt` (`started_at`); `FlattenSessionsOptions.timeOf` picks the clock for roots and sibling branches.
- `lib/session-date-groups.ts`: `groupEntriesByRecency(..., timeOf)` and `headRunCutoffMs` bucket by that clock.
- `app/chat/sidebar/index.tsx` and `sessions-section.tsx` pass `sessionCreatedAt` when the sort key is created.

## Code

`apps/desktop/src/lib/session-branch-tree.ts`, `session-date-groups.ts`, `app/chat/sidebar/index.tsx`, `sessions-section.tsx`.

## Tests

- `lib/session-date-groups.test.ts` ('groupEntriesByRecency with the created clock'): an old chat replied to an hour ago buckets by `started_at`, with a control for the default clock.
- `app/chat/sidebar/sessions-section-created-sort.test.tsx`: under the created sort rows order by `started_at`, a row does not move when an older session gets activity, and the default 'updated' sort still orders by activity.

## Applying

Desktop-only. Standalone, but touches the sidebar files other patches touch, so apply in order.

## Gotchas

- A session's displayed age label still reads last activity; only ordering and dividers follow creation.

## Plugin?

No. The sort lives in core sidebar rendering.
