# Test-suite deflakes and hardening

Patches: **2432, 2434, 2441, 2442, 2443, 2449, 2450, 2477**.

## Behavior

Test-only changes; no runtime behavior changes. Each removes a race, leak, or untested mutant:

- **2432**: the `tui_gateway` turn fixtures (`test_goal_command`, `test_refused_turn_retry`, `test_relay_live_author`, `test_update_pause_superseded`) stub `kanban_session_activity._refresh_links_in_background`, so running turn threads inline does not run the process-lifetime ledger refresher inline.
- **2434**: new `test_kanban_priority_first_head.py` kills two surviving mutants of `kanban_priority_prepare._inventory`: replaying from the last page head instead of the first, and dropping the `page['head'] < head` regression check. Capture and verify must refuse relevant task changes (priority, origin, terminal status, delete, insert) landing between the first and last page head, accept quiet/busy captures, pin replay cursors to `[1, 3]`, and refuse a regressing page head.
- **2441**: in `test_responses_disconnected_identity`, `release.set()` moved after the client disconnect is observed, so normal completion no longer races the client close.
- **2442**: `test_update_custody._state` treats `ProcessLookupError` as a reaped process (reaping after opening procfs stat gives ESRCH, before it ENOENT). New Linux-only test reaps a real child at both boundaries.
- **2443**: `test_startup_watchdog` escort stand-down test holds the escort with events instead of sleeping, and joins the escort before asserting exit codes.
- **2449**: `test_status_active_sessions_cache` writes within the racy stat window (pinned `time.time_ns`, optionally a same-tick stat signature) instead of settling the files, and asserts three opens.
- **2450**: the replica fixture in `test_kanban_show_replica` closes its connection after commit so GC cannot checkpoint the WAL after the byte snapshot; a regression test with GC disabled pins this.
- **2477**: `test_review_summary_callback` teardown sets each `PollerStop` of leaked `tui-notif-poller-*` threads, joins them under `DEFAULT_TIMEOUT`, and asserts none remain alive (the leaked poller previously kept pytest from exiting, hitting a per-file timeout).

## How it works

Fixture-level monkeypatches and synchronization (events, joins, closing contexts, `gc.disable`) replace timing assumptions. No production module changes.

## Code

`tests/tui_gateway/test_goal_command.py`, `test_refused_turn_retry.py`, `test_relay_live_author.py`, `test_update_pause_superseded.py`, `test_review_summary_callback.py`; `tests/hermes_cli/test_kanban_priority_first_head.py`, `test_update_custody.py`, `test_update_custody_observation.py`, `test_status_active_sessions_cache.py`; `tests/gateway/test_responses_disconnected_identity.py`, `test_startup_watchdog.py`; `tests/tools/test_kanban_show_replica.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): the files listed under Code are themselves the changed tests. 2434 exercises the internal `kanban_priority_prepare` capture/verify API; 2450 additionally exercises the `kanban_tools._handle_show` tool path. No CLI, REST, or terminal path is claimed beyond that.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- 2432 depends on `_refresh_links_in_background` existing in `kanban_session_activity` (see [kanban-stale-link-scopes-2491.md](kanban-stale-link-scopes-2491.md)).
- 2442's reaping tests are marked `platforms("linux")` since they use `/proc`.
- 2434 was written against an existing priority feed/prepare suite; it imports `Busy` and `Pages` helpers from those test modules.

## Plugin

Core change; no plugin required.
