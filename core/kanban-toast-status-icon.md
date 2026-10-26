# Kanban task toasts: status icon and Open-to-task

Patches:

- `0058`: the toast's Open action opens the event's board with that task's drawer open.
- `0062`: the toast leads with a status icon; the OS notification title gets a matching emoji.

## Behavior

Since **0105**, the bundled plugin suppresses completion delivery entirely and blocked in-app toasts
(away-only OS notification retained). Blocked cards instead live in the sidebar; see
[kanban-blocked-sidebar.md](kanban-blocked-sidebar.md). Failure/retry/triage toasts retain their icons and Open action.

The underlying icon mapping introduced by 0058/0062 remains available when a notification is delivered:

| Event | Icon |
|---|---|
| `completed` | green check (done) |
| `blocked`, `timed_out` | yellow `?` (waiting on someone / will retry) |
| `block_loop_detected` (routed to triage), `crashed`, `gave_up` | red X |

The native OS notification (fired while you're away from Hermes) gets the same glyph as a title prefix:
✅ / ❓ / ❌.

The toast's **Open** button (`0058`) goes to the Kanban page, switches to the board the event came from, and opens
that task's drawer. Before, it only navigated to `/kanban`, which showed whatever board the page last had open and
no task. If the event has no task id, Open just navigates as before.

## How it works

`completion-notify.ts` already maps each terminal event kind to a toast. Each entry now also carries a codicon name
(`pass-filled`, `question`, `error`) and an `accentColor` (`var(--ui-green)`, `var(--ui-yellow)`, `var(--ui-red)`),
which `host.notify` already supports: the toast renders the codicon instead of the kind's default icon and tints it.
OS notifications can't take a custom icon, so the title gets an emoji instead. No backend change.

Open action (`0058`): `notifyOne` now receives the board `slug` from `onKanbanEventsFrame`. The action's `onClick`
sets `$openTaskRequest` to `{ board: slug, taskId }` (when there is a task id) and then calls
`host.navigate('/kanban')`. `KanbanBoardPage` in `board.tsx` watches the atom with `useValue`: when it is set, it
sets `$boardSlug` to the request's board if that differs, opens the drawer with `setOpenId(taskId)`, and clears the
atom so a later remount doesn't reopen it. The atom lives in its own module, `open-task.ts`, so
`completion-notify.ts` can set it without importing React or creating an `api.ts` import cycle.

## Code

- `apps/desktop/src/plugins/kanban/open-task.ts` (new, `0058`): `OpenTaskRequest` and the `$openTaskRequest` atom
  (`atom` from `@hermes/plugin-sdk`).
- `apps/desktop/src/plugins/kanban/board.tsx` (`0058`): the effect in `KanbanBoardPage` that consumes the request.
- `apps/desktop/src/plugins/kanban/completion-notify.ts`: `NotifySpec` (`icon`, `accentColor`, `glyph`),
  `DONE` / `WAITING` / `TRIAGE` presets in `TERMINAL_NOTIFY`; `notifyOne(slug, kind, spec, ev)` and its Open
  action (`0058`).

## Tests

`npx vitest run src/plugins/kanban/completion-notify.test.ts` (from `apps/desktop`):

- `status icon` table covers every terminal kind (toast icon + tint, OS title prefix) (`0062`).
- "Open action parks the event board + task so the page opens its drawer" (`0058`): the atom is empty until the
  click, then holds `{ board, taskId }`, and the page navigates to `/kanban`. The test's `@hermes/plugin-sdk` mock
  now also exports `atom` (from `nanostores`), which `open-task.ts` needs.

The board-side effect in `board.tsx` has no test of its own.

## Applying

`git am -3 --empty=keep 0058-*.patch 0062-*.patch`, in that order: `0062`'s context lines in `completion-notify.ts` include
`0058`'s changes. I did not check whether `0058` depends on earlier fork patches to `completion-notify.ts` or `board.tsx`; use `git am -3 --empty=keep`.

## Gotchas

- Only the toast's Open button opens the task. The native OS notification is not changed by `0058`; what clicking
  it does is not covered by these patches.
- The request is cleared only when the board page consumes it. If navigation to `/kanban` doesn't mount the page,
  the request stays set and fires the next time the page mounts.
- To recolor a kind, change its preset in `TERMINAL_NOTIFY`; `crashed`/`gave_up` share the red X with triage.
