# One Kanban board across every gateway

Patch: `0090`

## Behavior

- With more than one gateway registered in Desktop (local, ssh, cloud), the Kanban page shows the selected board's
  cards from **every** gateway at once, whichever gateway is active.
- Each card's footer ends with the same device glyph the sidebar session rows use (laptop / Mac mini / cloud),
  which shows where the task lives and runs.
- Filter menu: **All gateways** / one gateway (persisted), each with its glyph. A gateway that didn't answer gets a
  ⚠ in the menu and an amber count chip in the header; hover the chip to see the error.
- Board switcher: the union of boards across gateways, card totals summed, glyphs for the gateways that have each
  board. Rename/settings/export/delete are offered only when the active gateway has that board.
- Editing a card (move, edit, comment, reassign, reclaim, delete, attach, estimate, bulk) goes to the gateway that
  owns it. Bulk actions are split per gateway.
- New tasks are created on the gateway picked in the filter, or on the active gateway when the filter is set to
  All. The dialog shows "On <gateway>". Assignee and parent choices come from that gateway.
- With a single gateway, nothing changes (the old code path runs).

## How it works

`fetchBoard` fans out `GET /api/plugins/kanban/board?board=<slug>` to every registry connection through the plugin
REST door (`ctx.rest(path, { connectionId })`) and merges the columns with `mergeBoards`. Merging tags each task
with `gateway` and re-sorts queue lanes by priority DESC, created_at ASC, and history lanes newest-first. The
board's `''` alias resolves against the active gateway, so remote gateways show the same board instead of their
own current one. Each read records `task id → {gateway, board}`, and every write uses that map to reach the owner.
Remote reads have a hard 6 s deadline, because the bridge's `timeoutMs` doesn't cover a cloud gateway that is
waiting on an OAuth login. After a failure the gateway is skipped for 60 s. A missing board counts as "no cards",
not an error. The events socket stays on the active gateway, and the page polls every 15 s while several gateways
are registered.

## Code

- `apps/desktop/src/api/plugins.ts`: `PluginRestOptions.connectionId`. A call pinned to a connection drops the
  ambient profile, because profile names belong to one gateway.
- `apps/desktop/src/sdk/index.ts`: `host.state.gateways` (id/kind/label only) and an export of `GatewayDeviceIcon`.
- `apps/desktop/src/plugins/kanban/api.ts`: `boardGateways`, `mergeBoards`, `mergeBoardLists`, `remoteRead`,
  `taskOwners` routing, `$gatewayFilter`, and a dispatcher nudge for each gateway.
- `board.tsx`: card glyph, filter section, header error chip, the create dialog's target gateway.
  `board-switcher.tsx`: per-board glyphs. `types.ts` and `i18n.ts` (en/ja/zh/zh-Hant): new fields and strings.

## Tests

`npx vitest run src/plugins/kanban` runs `api.gateways.test.ts`, which covers merge order and tagging, a missing
board vs. a failure, the union of board lists, the unchanged single-gateway path, per-owner write and bulk routing,
create-then-move on a remote, alias resolution, and the hung-gateway deadline plus cool-down. It was also checked
live in a probe window against the real local + mac-mini + nous-1 gateways: the mini's `bb` board renders with
Mac mini glyphs, the filter narrows correctly, and the unreachable nous-1 is flagged without blocking the board.

## Applying

`git am -3 0090-*.patch`. It touches the same kanban files as `0057` (drop attachments), so apply that one first.

## Gotchas

- Is it a plugin? No. The Kanban board is a bundled desktop plugin with no extension point for its data layer, and
  it needed two small SDK additions.
- Nothing is replicated. Each gateway keeps its own `kanban.db` and dispatcher, and the merge happens only in the
  view. The per-gateway web dashboard is unchanged. Tasks can't depend on tasks from another gateway.
- Live updates for remote gateways arrive on the 15 s poll, not instantly.
