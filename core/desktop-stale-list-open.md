# A stale-list row still opens and names its connection

Patches: 0585 (feature), 0586–0588 (tests and CI fixes that landed with it in daveey/hermes#126). Fixes a gap in the offline fallback ([hub-sync-replica.md](hub-sync-replica.md),
[desktop-sidebar-replica-live.md](desktop-sidebar-replica-live.md)) found on 2026-10-27.

## Why

The Desktop app's own backend stalled its event loop for 6–18 s under GIL pressure (2.3 GB RSS, 4 concurrent turns).
One sidebar session list failed, so `$sessionListFreshness` marked the local connection not fresh.
`mergeOfflineFallback` then turned every live local row into `{ ...row, offline_cached: true }` with no
`offline_gateway` and no `offline_synced_at`. `useOfflineFallback` refused to open those rows, and the toast read
"its gateway is offline, so this chat can't be opened right now (cached, never synced)". The gateway was running.

## What it does

- **Stale-list stamp.** A row that `mergeOfflineFallback` (`src/store/offline-sessions.ts`) keeps from a failed
  list, and that has no replica copy, gets `offline_stale_list: true` (a new `SessionInfo` field) as well as
  `offline_cached` and `is_active: false`. Replica rows never get it.
- **This Mac's own rows never yield to a replica copy.** The cold-start boot snapshot (`src/store/boot-snapshot.ts`)
  feeds this Mac's own gateway's replica rows into the same fallback, connection = the registry primary, and judges
  them asleep at the moment it was read. A retained row of the primary backend therefore always takes the stale-list
  stamp, even when such a copy exists: the copy says nothing about whether the local backend is up now. Rows of the
  primary carry no `connection_id` while replica copies name the primary, so the merge treats an empty
  `connection_id` as the primary when matching identities (otherwise the copy was added as a refused duplicate).
  A remote row with a replica copy keeps the copy (an asleep owner stays read-only or refused).
- **Open still tries.** Clicking a stale-list row (`useOfflineFallback`, `src/app/chat/sidebar/use-offline-fallback.ts`)
  shows "<connection> is not responding (its last session list failed). Trying to open this chat anyway." and then
  calls `resumeSession` live (`clearAsleepOpen`, never the read-only asleep open from
  [desktop-sidebar-replica-live.md](desktop-sidebar-replica-live.md)'s transcript cache), with resume's own timeout.
  `<connection>` is the registry label, for example "This computer", found through `sessionGatewayId`.
- **Replica rows are unchanged.** A replica row of an unreachable gateway opens read-only from this Mac's
  transcript cache when it has one. Without one, it is refused with the old "<gateway> is offline … can't be opened"
  notice.
- **Row mark.** The tooltip on a stale-list row reads "Not responding · <connection>, showing its last list" instead of
  "Offline · cached from its gateway, never synced".

## Tests

- `src/app/chat/sidebar/offline-fallback.integration.test.tsx`: a stale-list row of a busy local backend opens
  (resume is called), and the notice and tooltip name "This computer". A remote stale-list row that has a cached
  transcript resumes live, not read-only. A replica row with no cached transcript stays refused while its owner's
  list is also failing. A local row with an asleep boot-snapshot copy (with and without `connection_id`) still opens
  live, as one row, when the local list fails; a remote row whose replica says its owner is asleep stays refused.
- `src/store/offline-sessions.test.ts`: only rows without a replica copy get `offline_stale_list`, except this Mac's
  own rows, which get it over their boot copies with no duplicate row.
- `src/store/offline-session-actions.test.ts`: the notice names the connection and never says "offline",
  "never synced" or "its gateway".

## Gotchas

- Archive and rename of a stale-list row still take the offline hub-queue path (`requireOfflineOwner`), which needs
  `offline_gateway`, so they fail with "Offline session owner could not be resolved". This was already true before
  this patch.
- The watchdog alert for a stalled app backend lives in the private repo (`home/scripts/hermes_watchdog.py`,
  `desktop-app-backend`). It is not part of this patch.

## Follow-up patches (0605)

**0605** (`08d0c266d2`): the cold-start boot snapshot feeds this Mac's own replica rows into `mergeOfflineFallback`, judged asleep when read. When a later local list failed, a retained local row was replaced by that copy (`offline_cached` without `offline_stale_list`), so the open was refused ("<hub> is offline") or went read-only although the local gateway was running; with no `connection_id` the copy was added as a refused duplicate. Now a retained row of the primary backend never yields to a replica copy (it takes the `offline_stale_list` stamp and resumes live), and merge identities treat an empty `connection_id` as the registry primary. Remote rows with a replica copy keep the old behaviour. Code: `apps/desktop/src/store/offline-sessions.ts`. Tests: `offline-sessions.test.ts` (merge unit) and `offline-fallback.integration.test.tsx` (local row with and without connection_id opens live as one row; remote asleep replica stays refused); both new local tests fail on the parent.
