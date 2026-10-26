# Sessions open and run on the gateway that owns them

Patches:

- `patches/0040-fix-gateway-session.resume-only-materializes-keys-th.patch`: backend. `session.resume` only creates a
  row for an unknown session key that this backend minted. A key minted by another gateway gets 4007.
- `patches/0041-fix-desktop-resume-a-session-on-its-owning-gateway-a.patch`: desktop. A resume goes to the session's
  owning gateway, and a 4007 from the wrong gateway follows the owner instead of opening an empty chat.
- `patches/0045-fix-desktop-session-scoped-RPCs-route-to-the-owning-.patch`: desktop. Every session-scoped RPC
  (`slash.exec`, `process.list`, `session.control.read`, `session.activate`, ...) goes to the owning gateway, and
  follows it once on 4007/4001.
- `patches/0046-fix-desktop-a-session-tile-resumes-on-its-owning-gat.patch`: desktop. A session tile whose saved
  owner answers 4007 resumes on the real owner and repoints the tile.
- `patches/0047-fix-desktop-All-profiles-opens-a-foreign-gateway-s-s.patch`: desktop. In All-profiles mode, opening
  another gateway's session switches the selected gateway, like the normal view.

What you see: with two or more gateways in one Desktop sidebar (say a Mac mini and a MacBook), clicking a session that
lives on the other machine opens its real transcript on that machine, and the statusbar gateway switcher moves there.
Before, the click could send `session.resume` to the selected gateway. That gateway made an empty row for the foreign
id, so the chat opened empty and the gateway never switched. Background pollers then hit the wrong gateway every 5 s
and got "not in memory" (4001). Single-gateway installs behave as before.

Related: [multi-gateway-projects.md](multi-gateway-projects.md) (cross-gateway archive, move, projects and
`@session` refs; it defines "owning gateway").

## Behavior

- **Backend (0040).** `session.resume` for an unknown id that looks server-minted (`YYYYMMDD_HHMMSS_hex6`) only
  creates the row when this backend's own store recorded the key at `session.create`. Every gateway mints the same
  shape, so the shape alone is no proof. A foreign key now gets 4007 `session not found` and no row is written. The
  original reason for materializing (a draft created, never prompted, then the backend restarted) still works.
- **Resume by owner (0041).** Opening a session row tagged with another connection resumes only on that connection
  and selects it.
  - The tagged row's owner is saved as an owner hint when no hint exists yet. So a later resume still routes right
    while that gateway's rows are briefly missing from the unified list (its pooled backend is reconnecting).
  - If the resume has no exact owner and the selected gateway's store doesn't have the id, Desktop looks the owner up
    across connections before it sends anything: unified-list rows, then hints, then a by-id `getSession` probe of
    each other registry connection.
  - If the dispatched gateway answers 4007, Desktop does the same lookup. When it finds an owner, it pins that as the
    session's only hint and resumes there. That also switches the selected gateway. Each connection is tried at most
    once per open. If no connection owns the id, the old not-found handling runs.
- **Session-scoped RPCs (0045).** All callers of the shared router `requestForSessionProfile` use the same rules: the
  window dispatcher, the store pollers (via `requestForOwnedSession`), and tile actions.
  - Before dispatch: a connection-tagged unified-list row naming another registered gateway overrides a stale hint or
    tile route. So does an owner a previous follow already proved.
  - On 4007 (stored id unknown there), the owner is looked up the full way, including the by-id probe. On 4001 only
    rows and hints are checked, because a reaped runtime on its real owner also answers 4001. No probe runs per poll
    tick.
  - The RPC is retried once on the found owner. On success the hint is pinned, the open tile's route is repointed,
    and the correction is remembered so the wrong gateway isn't polled again.
  - A miss, or a failed retry, is cached for 60 s per session and gateway. Lookups are deduped. A session that is
    really gone costs one request per tick. The caller still gets the original error.
  - `session.resume` is never followed here; its callers rebind to the owner themselves (0041, 0046).
- **Tiles (0046).** `resumeTile` used to trust the tile's saved owner route. On 4007 it fell back to the read-only
  transcript, using the wrong gateway's empty prefetch. Now it looks up the owner, retries the resume there once,
  pins the hint, repoints the tile route, and re-reads the transcript from the owner.
- **All profiles (0047).** In All-profiles mode, a resume only dialed the owning backend (`openGatewayForAgent`), so
  the active profile wouldn't move. Across gateways that left the statusbar gateway switcher and the fleet rail ("All
  profiles on this gateway") on the old gateway. When the owner is a different registry connection, Desktop now
  activates it with `ensureGatewayAgent`. `$showAllProfiles` stays on. Opening another profile on the same gateway
  still uses the non-switching dial.

## How it works

- **0040** (Python):
  - `hermes_state_common.py`: new table `minted_session_keys (id TEXT PRIMARY KEY, minted_at REAL)`.
  - `hermes_state_gateway.py`: `SessionGatewayMixin.record_minted_session_key` (idempotent `INSERT OR IGNORE`, prunes
    rows older than `MINTED_SESSION_KEY_TTL_S` = 30 days on each write) and `was_session_key_minted`.
  - `tui_gateway/methods_session.py`: `_create_session` calls `_record_minted_key`, which writes into the session's
    own store (the one a later resume reads). Failures are logged, never fatal. `_resume_locate` adds
    `_was_minted_here(ctx)` to the conditions before `_resume_materialize_minted`. A lookup error counts as "not
    minted here".
- **0041** (`apps/desktop/src/app/session/hooks/use-session-actions/`):
  - New `owner-follow.ts`: `isStoredSessionNotFound` (code 4007, or a "session not found" message; a bare 4001
    doesn't count), `dispatchedConnectionId`, `findForeignSessionOwner(id, excluded)` (rows → hints → by-id probe;
    no probe with fewer than two registry connections), and `pinSessionOwner` (forget all hints, set one; two hints
    make `getSessionOwnerHint` ambiguous).
  - `index.ts`: `resumeSession` gains a `followedFrom` list and re-enters itself through `resumeSessionRef`. It saves
    the tagged row's owner (`sessionOwnerRouteFromRow`) as a hint, runs the pre-dispatch lookup, and follows the owner
    in the 4007 catch branch.
  - `utils.ts`: `cachedSessionRowCandidates` is split out of `cachedSessionRow`.
- **0045**:
  - `src/store/session-request-router.ts`: new `SessionOwnerFollower` interface (`redirect`, `follow`, `confirm`,
    `reject`) and `setSessionOwnerFollower`. `requestForSessionProfile` wraps the old body (now
    `dispatchForSessionOwner`): redirect before dispatch, then on error follow once and confirm or reject.
    `followableSessionRpc` skips `session.resume` and calls without a `session_id`.
  - `owner-follow.ts`: `sessionRpcOwnerFollower`, registered at module load. It keeps `followedOwners`,
    `noForeignOwnerUntil` (`NO_FOREIGN_OWNER_TTL_MS` = 60 s) and deduped `ownerLookups`, each capped at 256 entries.
    Also `routedConnectionId`, `repointSessionTileOwner` and `noteRpcStoredSessionId`. `findForeignSessionOwner` gets
    a `probe` option. Everything is inert below two registry connections.
  - `src/app/contrib/session-rpc-dispatcher.ts`: calls `noteRpcStoredSessionId` so a runtime id in params can be
    mapped back to the stored id the owner records key on.
- **0046**: `src/app/contrib/hooks/use-session-tile-delegate.ts`: `resumeTile` wraps the resume in
  `singleFlightSessionResume` with a 4007 catch that uses `findForeignSessionOwner`, `pinSessionOwner`,
  `repointSessionTileOwner` and a fresh `getLatestSessionMessages` prefetch from the owner.
- **0047**: `use-session-actions/index.ts`: a `switchesGateway` check (resolved connection differs from the current
  `$connection` and the registry has more than one connection) skips the All-profiles non-switching branch.

## Tests

- `tests/hermes_state/test_minted_session_keys.py` (new, 0040): record/lookup is idempotent and creates no sessions
  row; records older than the TTL are pruned.
- `tests/tui_gateway/test_session_resume_materializes_minted_key.py` (0040): a key minted here resumes after a
  restart; `test_resume_foreign_gateway_key_4007_without_phantom_row`; the lazy-watch and other-profile gates are
  isolated by recording the key first.
- `apps/desktop/src/app/session/hooks/use-session-actions.owner-gateway.test.tsx` (new in 0041, extended by 0047):
  each gateway is mocked per connection and per method. Cases: a tagged row resumes only on its owner; navigation
  keeps the owner after the row leaves the list; a hint-only restore; a stale local hint loses to a tagged row; 4007
  follows the owner; 4007 with no owner keeps not-found handling; an unlisted id is found by probe; a local control;
  and the two All-profiles cases (switches gateway, and a same-gateway other-profile control).
- `use-session-actions.test.tsx` (0041): one expectation changed. The row's own owner is now kept as a hint.
- `apps/desktop/src/app/contrib/session-rpc-dispatcher.owner-follow.test.ts` (new, 0045): stale hint and stale tile
  route never touch the wrong gateway; 4007 probes once and retries; 4001 follows a hint and repoints the tile; no
  owner means no retry storm; local controls.
- `apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.owner-follow.test.ts` (new, 0046): a tile with a wrong
  saved owner resumes on the owner with a transcript; control with a correct saved owner.

```sh
scripts/run_tests.sh tests/hermes_state/test_minted_session_keys.py \
  tests/tui_gateway/test_session_resume_materializes_minted_key.py
cd apps/desktop
npx vitest run src/app/session/hooks/use-session-actions.owner-gateway.test.tsx \
  src/app/session/hooks/use-session-actions.test.tsx \
  src/app/contrib/session-rpc-dispatcher.owner-follow.test.ts \
  src/app/contrib/hooks/use-session-tile-delegate.owner-follow.test.ts
```

I checked that the patches apply; I did not run these tests for this doc.

## Applying

Order: `0040 → 0041 → 0045 → 0046 → 0047`.

- 0040 is backend-only and applies on its own. Apply it on every gateway, not just the one Desktop runs on: the
  Desktop follow logic relies on foreign gateways answering 4007.
- 0041 needs **0023** ([multi-gateway-projects.md](multi-gateway-projects.md)): its hunks touch the `archiveOwner` /
  `restoreOwner` lines 0023 added.
- 0045 extends `owner-follow.ts` from 0041. 0046 uses `repointSessionTileOwner` and `routedConnectionId` from 0045.
  0047 extends 0041's test file.

Checked with `git am -3 --empty=keep` on the fork's merge-base with upstream: `0023 0040 0041 0045 0046 0047` apply cleanly, and so
does the full series `0001`–`0047` in order. Without 0023, 0041 fails.

Rebuild Desktop after applying (`hermes desktop --build-only --force-build`), and restart each gateway for 0040.

## Gotchas

- **Old gateways still fabricate rows.** A gateway without 0040 answers a foreign key with an empty session instead
  of 4007. Desktop then sees no error to follow. The pre-dispatch lookup in 0041 avoids most of these sends, but not
  all.
- **Keys minted before 0040** are not in `minted_session_keys`. A never-prompted draft from before the upgrade that
  then lost its backend gets 4007 instead of being materialized.
- **Minted-key records last 30 days.** A never-prompted draft older than that can't be materialized after a restart.
- **Phantom rows made before 0040 stay.** The patch stops new ones but doesn't delete old empty rows on the wrong
  gateway. Old `local` hints and tile routes from that time are corrected the first time they are used (0045, 0046).
- **4001 never triggers a probe.** If the only record of the owner is on the remote store (no row, no hint), a
  runtime-id RPC that gets 4001 is not followed. A later 4007 or resume finds it.
- **The by-id probe uses profile `default`.** `findForeignSessionOwner` calls `getSession(id, { connectionId,
  profile: 'default' })`. Whether that finds sessions stored under other profiles depends on the backend's lookup; I
  didn't verify it.
- **The probe is sequential.** Each other registry connection is tried in turn, so an unreachable gateway slows a
  4007 follow until its request fails.

## Plugin?

No. The fixes live in `session.resume`, the shared session-RPC router, `resumeSession` and the tile delegate. The
desktop plugin SDK and backend plugin hooks reach none of them.
