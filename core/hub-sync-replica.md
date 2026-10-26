# Desktop reads the hermes-hub replica for offline gateways

Patches:

- `patches/0219-feat-desktop-hermes-hub-replica-reads-owner-lookup-v.patch`: `apps/desktop/src/store/hub-sync.ts` (the
  Desktop's door to the `hub-sync` backend plugin), owner lookup consulting the replica, Kanban board fallback to
  the replica for a gateway whose live read fails.
- `patches/0220-feat-desktop-sidebar-shows-offline-gateways-sessions.patch`, `0229`, `0232`: the original standalone
  "Synced from other gateways" sidebar section (and its SDK routing). **Superseded by 0277**, which deletes the section.
- `patches/0277-feat-desktop-cached-sessions-as-offline-marked-rows-.patch`: cached sessions of unreachable gateways join
  the NORMAL sidebar list as offline-marked rows; the standalone section is removed. Also bumps the hermes-hub client
  to 1.4.0 (separate repo) so the collector stamps the owner's `automated` policy.

Needs the public `plugins/hub-sync` backend plugin (this repo) on the window's primary gateway and hermes-hub
syncing there. Without either, every read answers `null`/`[]` and the Desktop behaves exactly as before.

## Behavior

- **Owner lookup.** Opening a stored session id the window can't place (`findForeignSessionOwner`) asks the primary
  gateway's replica (`GET /api/plugins/hub-sync/where/{id}`) first and routes to that gateway without probing every
  connection. Unknown id: the old by-id probe. A single-gateway Desktop is unaffected.
- **Kanban board.** When one gateway's live board read fails, its cards come from the replica (`/tasks`), marked
  "from hub" with their sync age. A comment on a replica card is queued as a hub request.
- **Cached sessions in the normal sidebar (0277).** There is no history section. The hub-sync plugin is a poller
  service that publishes replica rows (`publishOfflineSessions`, `store/offline-sessions.ts`) as `SessionInfo`
  fallback rows. `ChatSidebar` merges them into `$sessions` before the existing pipeline, so profile/gateway
  selection, the automated/test toggle, search, pinning, grouping, lineage and ordering apply unchanged.
  - Eligible: ordinary titled user chats of a gateway that has a registry connection and NO live rows right now.
  - Never added: sources in the shared `SIDEBAR_EXCLUDED_SOURCES` (cron/acp/kanban/oneshot/subagent/tool/messaging),
    archived, hidden, empty, internal-child and untitled rows (no raw ids as titles), unregistered gateways.
    Rows whose owner `automated` stamp is missing count as automated (hidden unless the toggle is on).
  - Compression chains collapse to the tip. Recency is the session's real `last_active`, never the sync time.
  - Rows are marked offline (cloud mark + "synced Xm ago", `data-testid=session-row-offline`) and never
    `is_active`: a stale running turn is not shown as live.
  - Live wins: any connection that has live rows drops all its cached rows (reconnect, no duplicates); identity is
    (connection, profile, id) and lineage ids dedupe too.
  - Clicking a cached row does not call resume; it shows an honest "gateway offline, cannot open" notice.
    Archive and Rename on a cached row queue an owner request on the hub (pending marker); pin is local only.

## Code

`apps/desktop/src/store/{hub-sync,offline-sessions,offline-session-actions}.ts`, `lib/{session-source,synced-ago}.ts`,
`app/chat/sidebar/{use-offline-fallback.ts,session-row-offline.tsx,index.tsx,session-row.tsx}`,
`app/session/hooks/use-session-actions/`, `plugins/hub-sync/{plugin.tsx,api.ts,model.ts}`, `sdk/hub-sync.ts`,
`plugins/kanban/*`.

## Tests

Run in `apps/desktop`: `npx vitest run src/store/offline-sessions.test.ts src/store/offline-session-actions.test.ts
src/app/chat/sidebar src/plugins/hub-sync src/store/hub-sync.test.ts src/plugins/kanban`; full suite 262 files /
2962 tests pass (9 pre-existing electron/* and mac-sign environment failures on the hub); `tsc --noEmit` and eslint clean.

- `store/offline-sessions.test.ts` (16): source exclusion, archived/hidden/empty/untitled/unregistered, tip collapse,
  pinned and unstamped-automated, automated off/on, profile selection, live-over-cache and reconnect, same id on
  another gateway/profile, lineage dedupe, reference identity, activity-based ordering, no stale running claim.
- `app/chat/sidebar/offline-fallback.integration.test.tsx` (6, RED before the change): normal row with offline mark
  and no standalone section, automated toggle, history/untitled excluded, honest refusal to open, live row opens,
  reconnect without duplicates.
- `store/offline-session-actions.test.ts` (4): archive/rename of cached rows become hub requests.
- `plugins/hub-sync/plugin.test.ts`: the plugin registers no sidebar section.

Untested / limits: unregistered gateways are skipped; untitled rows are skipped; other row-menu verbs on cached rows
hit normal offline errors; the backend side of an applied request is hermes-hub's.

## Applying

`git am -3` in order, after 0090 (Kanban all-gateways) and 0093; 0277 replaces the section from 0220/0229/0232.

## Gotchas

- Deploy the hermes-hub client 1.4.0 on every gateway; until then replica rows lack `automated` and are hidden
  unless the automated toggle is on.
- A gateway that is reachable but lacks the `hub-sync` plugin counts as live only through its rows in the live list.

## Plugin?

Partly: the REST is the `hub-sync` agent plugin, and the Inbox/Session Tasks consumers are disk plugins. The
sidebar merge reads core's `$sessions`, so it ships on the fork.
