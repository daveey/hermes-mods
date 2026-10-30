# Desktop sidebar: live local rows and activity stamps while on another gateway

Patches: 0984, 1113, 1114, 1124.

## Behavior

- The move-to-project shortcut has a browser-host default (Ctrl/Cmd+Alt style chord) so it does not collide with the browser's private-window chord; native Desktop keeps its chord (984).
- With another gateway active, an awake owner's projects-tree row replaces its stamp-less replica copy, so parked work paints the grey waiting stripe; an asleep owner's cached row stays cached (1113).
- When the window is on a non-primary connection, the session list also loads the primary's recents (pinned included), tags them with the primary's id and folds them in; a failed primary read leaves the window's page untouched (1114).
- Awake replica rows outside the projects tree get their owner's activity stamps through a new endpoint (1124).

## How it works

984 edits the keybind table in `actions.ts`. 1113 passes projects-tree rows into `mergeOfflineFallback`. 1114 adds `primary-recents.ts` (`withPrimaryRecents`) used by `refreshSessions`. 1124 adds REST `GET /api/profiles/sessions/stamps?profile=&ids=` (same annotators as the list endpoints; unknown profile is 404) and `owner-row-stamps.ts`, which batches per owner (100 ids), throttles each row to one ask per `STAMP_FRESH_MS`, backs off after a failed request and keeps prior stamps; a tree copy still wins and asleep rows are untouched.

## Code

- `apps/desktop/src/lib/keybinds/actions.ts` (984)
- `apps/desktop/src/store/offline-sessions.ts`, `offline-session-owner.ts`, `app/chat/sidebar/use-offline-fallback.ts` (1113, 1124)
- `apps/desktop/src/api/sessions.ts`, `app/session/hooks/primary-recents.ts`, `use-session-list-actions.ts` (1114)
- `apps/desktop/src/store/owner-row-stamps.ts`, `hermes_cli/web_routers/profiles.py` (1124)

## Tests

Tests below were identified from the patch source in this documentation pass; they were **not executed** here. Where a patch lists no test name, it edited existing tests or touched untested code and nothing is claimed for it. 1113 (`store/offline-sessions.test.ts`): `describe('an awake owner whose backend reported the session in its projects tree')` with `it('uses the stamped tree row instead of the replica copy')` and the asleep-owner case. 1114: `app/session/hooks/primary-recents.test.ts` (`describe('withPrimaryRecents')`), `use-session-list-actions.test.tsx` (`describe` for this Mac's own chats while on another gateway) and `api/sessions.test.ts`. 1124: `store/owner-row-stamps.test.ts` (`describe('owner row stamps')`, including batching over 100 ids and backoff), `offline-sessions.test.ts`, and Python `tests/hermes_cli/test_profiles_session_stamps.py::test_stamps_carry_kanban_cwd_and_turn_live_for_named_ids` and `::test_unknown_profile_is_404`. 984 adds no test; the browser-defaults test update is in the test-port doc (987).

## Applying

These patches belong to the ordered fork series that follows an upstream merge. Apply the whole series with `git am -3 --empty=keep` from its recorded base; each patch depends on the ones before it, and no independent application of this subset was tested. The ported tests only make sense against the post-merge upstream names they reference.

## Gotchas

The stamps endpoint exists only on newer backends; an older owner returns an error and the client backs off. Only the paths in the tests above are claimed; the native-Desktop chord is not tested by these patches.

## Could this be a plugin instead?

No: renderer state plus a core REST route.
