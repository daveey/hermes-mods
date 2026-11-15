# Off-loop boot recovery and CLI session-switch ordering

Mirror snapshot 4059; covered ordinals: 3902, 3904, 3921.

## Behavior

- Native boot recovery resolves pending route owners off the event loop and avoids scanning routes when no admission needs recovery.
- Startup stale-route pruning fetches only ended-session reasons in batches per owning database.
- The classic CLI publishes the new current session before switch listeners execute, so a late inventory settlement for the old session cannot print its warnings or adopt its toolsets.
- A test-only signal-handling change waits for a completed diagnostic dump before shutting down the child.

## How it works

`gateway/run_runtime.py:_pending_native_routes` performs admission, checkpoint, and logical-owner reads via `asyncio.to_thread`; adapter lookup and recovery stay on the event loop. `gateway/session_persistence.py:_ended_reasons` groups IDs by owning database and uses chunked `id/end_reason` reads, retaining `get_session` fallback for handles lacking the real batch API.

`hermes_cli/gateway_session_store.py:set_current` assigns `current` before swapping and invoking listeners. The diagnostic test replaces a fixed sleep with readiness/dump-completion handshakes and graceful child exit.

## Code

- `gateway/run_runtime.py`
- `gateway/session_persistence.py`
- `hermes_cli/gateway_session_store.py`

### Mirrored commits

- 3902: `ed75e24bda373b74f1c65a8d0f4cfa374d1fdc66` — Moves boot owner lookup off-loop and batches stale-route end reasons.
- 3904: `5e87b950851d8b04382e17edcd7879d38fa8ba53` — Publishes CLI current session before switch listeners.
- 3921: `48ce28ab8e24ca91f95066ef5f68634c86599334` — Waits for completed SIGUSR2 diagnostics before child shutdown.

## Tests

- `tests/gateway/test_boot_recovery_off_loop.py`: recorded thread IDs prove database reads and owner resolution are off-loop; an empty pending set prevents route enumeration; real database pruning removes ended entries without calling full-row `get_session`.
- `tests/hermes_cli/test_cli_gateway_inventory_pending.py`: a listener triggers late old-session settlement during switching; no warnings are printed, no old toolsets adopted, and listeners are cleared.
- `tests/gateway/test_faulthandler_no_stderr.py`: the POSIX child installs its handler, completes the SIGUSR2 end marker, and exits normally before the existing dump assertions run. Teardown still kills a stuck child.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

Only blocking lookup moves off-loop, not adapter access. Batch pruning must use each route's owning store rather than an ambient profile. The signal test is POSIX-specific and does not establish support on other platforms.

## Plugin?

No. Session authority and UI notification ordering require changes in core owners.
