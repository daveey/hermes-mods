# Sidebar paints from a boot snapshot before the backend is up

Patch **0577** (source `6daea0b007`). Builds on [desktop-sidebar-replica-live.md](desktop-sidebar-replica-live.md) and
[hub-sync-replica.md](hub-sync-replica.md).

## Behavior

- Before: a cold start showed a blank window for about 7.7 s. The full-screen CONNECTING overlay waits for the local
  backend (about 6 s to boot) and every sidebar row (hub-sync replica rows, the projects tree) is served by that backend.
- Now Electron main reads the hub replica (`hub.db`, read-only) and the last saved projects tree before the backend
  exists, and the first window paints the sidebar from them at once. Rows of every gateway show, this one's included,
  as ordinary rows (never "offline").
- Live lists replace the snapshot rows per owner as soon as they are fresh; a plugin row with the same identity wins
  over the boot row; this gateway's boot rows are dropped once its own live list is fresh.
- The projects tree seeds only while empty. The live tree is saved (debounced, no running state) for the next launch.
- The CONNECTING overlay stays down on a cold boot whose sidebar painted from the snapshot (as it already does for a
  post-boot reconnect). With no snapshot it still shows.
- The hub-sync plugin publishes nothing when the replica read has no answer, so a failed read never wipes rows
  already showing.
- One `[sidebar-timing]` line in desktop.log: ms from process start to the first sidebar rows, to the first live
  list, and the rows' source.

## How it works

- `electron/sidebar-boot-snapshot.ts`: `readHubSessions()` is a port of hub-sync `hubview.py sessions()` (published
  and replica rows, pending requests, primary-mode shared-field overlay, activity order, row cap, `synced_at` stamping
  and the stale window). `hubDbPath()` follows `HERMES_HUB_STATE`, else `~/.local/state/hermes-hub`. A missing db
  answers null and is never created. The tree lives in `userData/sidebar-boot-snapshot.json`; a torn or foreign file
  is rejected. `installSidebarBootSnapshot` registers the IPC: `hermes:boot-snapshot:read` (sync, returns process
  start, hub rows, saved tree) and a save-tree channel that ignores malformed payloads.
- `electron/preload.ts` + `src/global.d.ts` expose the bridge; `electron/main.ts` installs it.
- `src/store/boot-snapshot.ts`: `applyBootSnapshot` seeds the sidebar fallback inputs (offline-sessions,
  offline-session-owner) and the projects store; `saveBootTree` writes the live tree; the `[sidebar-timing]` log.
- `src/components/gateway-connecting-overlay.tsx` skips the overlay when the snapshot painted.
- `src/plugins/hub-sync/api.ts` / `plugin.tsx`: a failed replica read returns null and publishes nothing.

## Code

`apps/desktop/electron/sidebar-boot-snapshot.ts`, `electron/main.ts`, `electron/preload.ts`, `src/global.d.ts`,
`src/main.tsx`, `src/store/boot-snapshot.ts`, `src/store/offline-sessions.ts`, `src/store/offline-session-owner.ts`,
`src/store/projects.ts`, `src/app/chat/sidebar/index.tsx`, `src/app/chat/sidebar/use-offline-fallback.ts`,
`src/app/session/hooks/use-session-list-actions.ts`, `src/components/gateway-connecting-overlay.tsx`,
`src/plugins/hub-sync/api.ts`, `src/plugins/hub-sync/plugin.tsx`.

## Tests

Not re-run on the Linux mirror host (they need the Desktop node toolchain); they ship with the patch:

- `electron/sidebar-boot-snapshot.test.ts`: own and replica rows, newest first, archived and torn rows dropped;
  `synced_at` stamping; stale window; pending requests; shared-field overrides in primary mode; missing db answers
  null and is not created; row cap; `hubDbPath`; snapshot file round trip and rejection; the IPC read, read without
  db or tree, and malformed save.
- `src/store/boot-snapshot.test.ts`: own rows at once and never offline; another gateway's rows once the registry
  maps its hub name; asleep owner marked cached; tree seeded only while empty; nothing without bridge; plugin row
  wins; own boot rows dropped when live; debounced tree save; `[sidebar-timing]` line.
- `src/components/gateway-connecting-overlay.test.tsx`: cold boot with and without a painted snapshot.
- `src/plugins/hub-sync/api.test.ts` and `plugin.test.ts`: a failed read publishes nothing, then publishes once it answers.

## Applying

`git am -3` 0577 after 0537 and the hub-sync replica patches. The Electron main change needs an app rebuild and
restart; a disk-plugin hot reload cannot activate it.

## Gotchas

- The reader duplicates hubview's SQL shape; a change to the hub schema must be mirrored in both.
- Snapshot rows are only as fresh as the last hub pull; the stale window marks them rather than hiding them.
- First launch after install has no snapshot and shows CONNECTING as before.

## Plugin?

No. It needs the Electron main process and the pre-render store; the hub-sync half already is a bundled plugin.

## Follow-up patches (0623)

**0623** (`ff8ec1d78c`): replica recents showed "Subagent: ..." rows in Home, because hub replica rows (other gateways, and this gateway's own boot snapshot) only went through the source rule and a delegate child keeps its parent's source. The replica recents rule now drops rows the owner stamps `is_internal_child`. Exact-id opens are unaffected. Code: `apps/desktop/src/store/offline-sessions.ts`; test in `offline-sessions.test.ts`.
