# Sidebar rows of every gateway from the hub replica, at once

Patch: 0528. Part A of "instant session loads" (2026-10-27, approved by David). Builds on the hub-sync replica
fallback in [hub-sync-replica.md](hub-sync-replica.md) and needs hermes-hub client 1.7.1 or later on every gateway
(`hermes-hub watch` keeps the replica within about 2 s of each owner).

## What it does

- **Rows without waiting on SSH.** The sidebar shows every gateway's sessions from this Mac's hub replica right away
  on a cold start. `loadReplicaSidebarRows` (`src/plugins/hub-sync/api.ts`) no longer waits on the per-gateway
  `/status` reachability probes, each of which is an SSH round trip. They still run in the background and only
  invalidate retained live lists.
- **Awake owners give ordinary rows.** A replica owner that published within `OWNER_ASLEEP_AFTER_S` (180 s) is awake.
  Its rows are ordinary sidebar rows: `offline_cached` false, `is_active` from the owner's `running`, and they open the
  normal way. An owner silent longer than that, or a stale replica, is asleep: cached rows that are marked and never
  running, as before. See `ownerAsleep` / `rowFromRecord` in `src/store/offline-sessions.ts`.
- **Live lists still win.** A gateway's live list replaces its replica rows once it is fresh (`sessionOwnerIsFresh`).
- **Cheap polling.** The hub-sync poller (`src/plugins/hub-sync/plugin.tsx`) reads the replica cursor every 2 s and
  reloads rows only when it moved, plus a full reload every 30 s so owner freshness is re-evaluated.

## Tests

- `src/store/offline-sessions.test.ts`: awake rows are ordinary and show running; past the 180 s edge, stale or never
  synced is asleep and never running.
- `src/plugins/hub-sync/api.test.ts`: replica rows return while every reachability probe hangs; a fresh live list hides
  that gateway's replica rows.
- `src/plugins/hub-sync/plugin.test.ts`: reloads only when the cursor moves, and every 30 s.

## Measured (2026-10-27)

Renames on the mini reached the MacBook replica in 1.7–2.1 s over 5 trials with client 1.7.1 (corrected for the
+0.18 s clock offset). Before 1.7.1, with a 2 s publish gap, they took 1.3–3.3 s.

## Gotchas

- What a click does on an asleep (`offline_cached`) row belongs to the transcript-cache work (C1): it opens read-only
  from the local cache.
- `OWNER_ASLEEP_AFTER_S` must stay above the hermes-hub poll interval (60 s). The gateway record is republished every
  cycle, so 180 s allows two missed cycles.
