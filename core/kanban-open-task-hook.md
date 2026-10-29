# `window.__hermesKanbanOpenTask`: disk plugins open a Kanban card's drawer

Patch: `patches/0127-feat-desktop-kanban-window.__hermesKanbanOpenTask-ho.patch` (Desktop only).

What you see: a disk plugin can take you straight to a Kanban card, with its drawer open so you can edit the fields
or comment, on the card's own board and gateway. The consumer in this repo is
[desktop-plugins/task-mentions](../desktop-plugins/task-mentions/): its popup's "Open card" button calls the hook.
Without the hook a disk plugin can only navigate to `/kanban` and leave you to find the card.

## Behavior

- Core's kanban plugin installs `window.__hermesKanbanOpenTask(taskId, board?, gateway?)` when it registers.
- A call with a non-empty `taskId`:
  - when `gateway` (a gateway connection id) is given, records the task's owner (that gateway + `board`) so the
    drawer's first fetch goes to the owning gateway;
  - parks `$openTaskRequest = { board, taskId }` (`board` defaults to `''` = keep the current board);
  - navigates to `/kanban`, where the board page switches to `board` if it differs, opens the card's drawer and
    clears the request;
  - returns `true`.
- An empty or non-string `taskId` returns `false` and does nothing (no navigation).
- Passing no `gateway` (the card is on the routed gateway) skips the owner hint.
- The hook is removed when the kanban plugin is disposed, but only if it is still the function this plugin installed.
- Consumer fallback (task-mentions): on a core without the hook, or when it returns `false`, the plugin navigates to
  `/kanban` and shows an info toast saying the card can be opened directly after the next Hermes update.

## How it works

`$openTaskRequest` already existed: the completion toast's Open action and the blocked-cards sidebar use it, and
`board.tsx` consumes it in an effect (switch `$boardSlug`, `setOpenId(taskId)`, reset to `null`). 0127 only exposes
a global entry point to it. Disk plugins cannot import core modules, so the hook lives on `window`, the same pattern
as other `window.__hermes*` hooks. The owner seed matters for cross-gateway cards: the board API resolves each task's
gateway from the `taskOwners` map it fills while reading boards, and a card opened from a chat before the board has
loaded it would otherwise be fetched from the routed gateway.

## Code

- `apps/desktop/src/plugins/kanban/open-task.ts`: `OPEN_TASK_HOOK = '__hermesKanbanOpenTask'`,
  `installOpenTaskHook({ navigate, rememberOwner? }, target = globalThis)` returning the disposer.
- `apps/desktop/src/plugins/kanban/api.ts`: `rememberTaskOwner(id, board, gateway)` sets `taskOwners`.
- `apps/desktop/src/plugins/kanban/plugin.tsx`: `ctx.onDispose(installOpenTaskHook({ navigate: path =>
  host.navigate(path), rememberOwner: rememberTaskOwner }))` in `register`.
- Consumer: `desktop-plugins/task-mentions/plugin.js` `openCard` (hermes-mods commit 6db4272).

## Tests

`apps/desktop/src/plugins/kanban/open-task.test.ts` (4 cases; passed on the full 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126, 0127 series, Oct 17, 2026 PT,
`cd apps/desktop && npx vitest run src/plugins/kanban/open-task.test.ts`):

- parks `{ board, taskId }` on the named board and navigates to `/kanban`;
- a `null` board parks `''` (current board); an empty id returns `false`, parks nothing and does not navigate;
- with a gateway it calls `rememberOwner(taskId, board, gateway)`; without one it does not;
- the disposer removes only its own hook (leaves a replacement in place, deletes its own).

Untested: the cross-gateway drawer fetch through the seeded owner. Only the unit seam (`rememberOwner` called with
the right arguments) is tested, not an end-to-end board test that opens a remote card. The board-side consumption of
`$openTaskRequest` is older code and is not re-tested here. The task-mentions consumer and its fallback toast are
not covered by any test in this patch.

## Applying

Needs `$openTaskRequest` in `open-task.ts` and its consumer in `board.tsx` (present at the fork's merge-base plus the
toast/blocked-section patches, [kanban-toast-status-icon.md](kanban-toast-status-icon.md) and
[kanban-blocked-sidebar.md](kanban-blocked-sidebar.md)), and `taskOwners` in `api.ts` from
[kanban-all-gateways.md](kanban-all-gateways.md) (0090). Verified: applies with `git am -3 --empty=keep` on top of 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126 at
merge-base `4957bbd0a0` (Oct 17, 2026 PT). Desktop needs a rebuild.

## Gotchas

- `board` is a board slug, not a display name.
- The hook exists only while the kanban plugin is enabled. Consumers must feature-detect it
  (`typeof window.__hermesKanbanOpenTask === 'function'`) and fall back.
- Pass `gateway` only for a card on a non-routed gateway; a wrong connection id sends the drawer's first fetch to
  the wrong gateway.

## Plugin?

No. The drawer state and the `taskOwners` routing map are private to core's kanban plugin; a disk plugin cannot
reach them without this exported hook.
