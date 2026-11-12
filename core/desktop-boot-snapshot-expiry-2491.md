# Desktop boot-snapshot rows expire once their owner answers

Patches: **2474**.

## Behavior

An archived chat no longer reappears in the Desktop sidebar when a remote owner's session list goes stale. Once a gateway (or profile) has answered a live session list at least once in this app launch, its rows from the launch-time boot snapshot are dropped from the sidebar fallback for the rest of the launch. Owners that have never answered keep their boot rows, so the cold-start paint is unchanged.

## How it works

`offline-sessions.ts` adds `$answeredSessionOwners`, a never-cleared set of owner keys (`connection\0profile`, profile `*` meaning every profile there). `publishFreshness` calls `noteAnsweredOwners`, which adds every owner whose freshness flag is `true`. `sessionOwnerAnswered(connection, profile, answered)` matches the profile key or the `*` key. In `boot-snapshot.ts`, `$sidebarFallbackSessions` now also depends on `$answeredSessionOwners` and filters out boot rows whose owner has answered, in addition to rows the hub-sync plugin already carries. Before this, only the primary's own boot rows expired; a remote owner's boot rows stayed eligible and filled the gap whenever its list went stale, even though the replica correctly omits archived rows. See [sidebar-boot-snapshot.md](sidebar-boot-snapshot.md) for the snapshot itself.

## Code

`apps/desktop/src/store/boot-snapshot.ts`, `apps/desktop/src/store/offline-sessions.ts`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `apps/desktop/src/store/boot-snapshot.test.ts` (an answered owner's boot row stays out after a stale list, via a failed list and via gateway-unavailable; a never-answered owner's boot row is kept). Other sidebar paths (replica rows, live rows) are untested by this patch.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- "Answered" is per launch and never reset, so a chat that exists only in the boot snapshot and is unreachable after its owner answered once is not shown while that owner is stale.
- Test reset (`_resetOfflineSessionsForTests`) clears the set.

## Plugin

Core change; no plugin required.
