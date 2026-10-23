# Desktop reads the hermes-hub replica for offline gateways

Patches:

- `patches/0215-feat-desktop-hermes-hub-replica-reads-owner-lookup-v.patch`: `apps/desktop/src/store/hub-sync.ts` (the
  Desktop's door to the `hub-sync` backend plugin), owner lookup consulting the replica, Kanban board fallback to
  the replica for a gateway whose live read fails.
- `patches/0216-feat-desktop-sidebar-shows-offline-gateways-sessions.patch`: bundled Desktop plugin
  `apps/desktop/src/plugins/hub-sync/` — the left-pane "Synced from other gateways" section.

Needs the public `plugins/hub-sync` backend plugin (this repo) on the window's primary gateway and hermes-hub
syncing there. Without either, every read answers `null`/`[]` and the Desktop behaves exactly as before.

## Behavior

- **Owner lookup.** Opening a stored session id the window can't place (`findForeignSessionOwner`) asks the primary
  gateway's replica (`GET /api/plugins/hub-sync/where/{id}`) first and routes to that gateway without probing every
  connection, so a sleeping owner costs no wait. Unknown id → the old by-id probe. An excluded connection (the 4007
  sender) is never named. A single-gateway Desktop is unaffected.
- **Kanban board.** When one gateway's live board read fails, its cards come from the replica (`/tasks`), marked
  "from hub" with their sync age; a gateway that answers is read live only. A gateway whose hub name is unknown
  stays missing (no guessing). A comment on a replica card is queued as a hub request.
- **Sidebar section (0216).** Sessions of gateways that are not live right now — no successful hub-sync `/status`
  probe and no rows in the live sidebar list, or not registered on this Desktop at all — appear in a "Synced from
  other gateways" section between Pinned and Projects: greyed label, cloud glyph, `synced Xm ago` (tooltip says the
  gateway isn't reachable). Live data stays primary: the window's own gateway, any reachable gateway and any
  (gateway, session id) already in the live list are dropped; archived/hidden replica rows are dropped; duplicates
  are collapsed by (gateway, id). Hover buttons Pin/Unpin, Rename (inline input) and Archive queue an owner request
  (`POST /api/plugins/hub-sync/request {gateway, coll:'sessions', key, action, args?, base_version}`) on the primary
  gateway, which works with the hub down; the row then shows `pending` until the owner applies it. Clicking a row
  opens it through the owner route (`host.openSession(id, {route})`) when the gateway has a connection. Hidden when
  empty. Bundled plugin id `hub-sync`, on by default.

## How it works

- `store/hub-sync.ts`: hub gateway names ("mini") ↔ registry connection ids are learned from each connection's own
  `/status` and persisted in localStorage `hermes.desktop.hubGatewayNames.v1` (also read by the `inbox` and
  `session-tasks` disk plugins), with an id/label/host match as fallback. Every call is best-effort and never
  throws into a caller.
- `plugins/hub-sync/model.ts`: pure `replicaSessionsToShow`, `syncedAgo`, `sessionRequestBody`, `markRowPending`.
  `api.ts`: `loadReplicaSidebarRows(live)` reads `/sessions?limit=300` on the primary, probes the other connections'
  `/status` (success cached 5 min by `learnHubGatewayName`), and filters. `sessions-section.tsx` renders it with the
  SDK `SidebarSection`/`SidebarSectionRow`, polling every 60 s.

## Code

`apps/desktop/src/store/hub-sync.ts`, `app/session/hooks/use-session-actions/owner-follow.ts`,
`plugins/kanban/{api.ts,board.tsx,i18n.ts,types.ts}`, `plugins/hub-sync/{plugin.tsx,sessions-section.tsx,api.ts,model.ts}`.

## Tests

Run in `apps/desktop` (vitest, Oct 23, 2026; `npx tsc -p . --noEmit` clean):
`npx vitest run src/store/hub-sync.test.ts src/plugins/kanban src/plugins/hub-sync src/app/session/hooks/use-session-actions/owner-follow.hub.test.ts src/contrib`
→ 29 files, 231 tests passed.

- `store/hub-sync.test.ts`: name learning/persistence, no-plugin → nothing, fallback match never steals a proven
  name, re-added connection takes the name over; `hubSessionOwner` mapping, exclusion, live-over-archived, caching,
  unregistered/missing/error → undefined; `hubRequest` posts to the primary.
- `owner-follow.hub.test.ts`: replica answers without probing; asleep owner; fallback probe; excluded connection;
  single-gateway inert.
- `plugins/kanban/api.hub-fallback.test.ts`: failed live read filled from replica (marked); answering gateway stays
  live; unknown hub name stays missing; replica-card comment → hub request; `hubBoardFromTasks` lanes.
- `plugins/hub-sync/model.test.ts`: not-live gateways only, local never; live (gateway, id) dropped; dedupe by
  (gateway, id) keeps the same id on another gateway; archived/hidden dropped; `syncedAgo`; request body with
  `base_version`; optimistic pending marker.
- `plugins/hub-sync/sessions-section.test.tsx`: replica read on the primary, reachable gateway excluded; a gateway
  with live rows is live even when its probe fails; hub-sync unavailable → no rows; section renders nothing when
  empty and greyed rows with `synced 5m ago` otherwise; Archive / Rename (inline) / Unpin send the right request and
  Archive shows `pending`; click opens through the owner route.

Untested: a real Desktop window (no live render or click was exercised), the backend side of an applied request
(that is hermes-hub's), and strings are English only (no locale overlays; the section is plugin chrome).

## Applying

`git am -3` in order, after 0090 (Kanban all-gateways) and 0093.

## Gotchas

- "Not live" uses a cached `/status` success (5 min): a gateway that just went down keeps its live rows out of the
  section until the cache expires or its live rows vanish; the replica row then appears.
- A gateway that is reachable but lacks the `hub-sync` plugin counts as live only through its rows in the live list.

## Plugin?

Partly: the REST is the `hub-sync` agent plugin, and the Inbox/Session Tasks consumers are disk plugins. The owner
lookup and Kanban fallback live inside core stores, and the section reads core's `$sessions`, so they ship on the fork.
