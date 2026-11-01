# Runtime follow-ups in mirror 1428–1468

Patches covered here: **1429, 1444, 1445, 1458, 1459, 1461, 1462, 1467**.
The related composer Escape fix, **1431**, extends [turn-stop-notice.md](turn-stop-notice.md).
This document describes the final combined behavior, including the removal in 1459 of an index briefly introduced in 1458.

## Behavior

- **1429 — recorded routing intent:** resuming a session whose row model disagrees with its recorded allocation restores the recorded route, subject to current managed policy. A completed failed agent build with no agent no longer excludes the chat from unloaded-notification sweeping; a full final-send notice can be persisted without a model call.
- **1444 — lineage receipts:** local lineage enumeration uses a partial index of multi-segment receipts rather than repeatedly parsing large single-segment policies after unrelated writes. Malformed receipts remain writable and do not contribute valid lineage projections.
- **1445 — sleep/wake:** Desktop detects a wall-clock tick gap greater than 30 seconds even without a power-monitor resume event. Background GETs are held through the existing settle/stagger window. Qualifying connection failures while suspended or during the wake grace become `PowerSettlingError`, not individual failure-stack log lines. Foreground requests, writes and server errors are not absorbed.
- **1458–1459 — session listing:** non-search recent-session listings seed their recursive lineage walk from distinct parent IDs, avoiding a second JSON-filter pass over childless rows. ID/title searches retain the all-candidate seed. The final series does **not** create the session-marker expression index from 1458 or promise its count-query speedup.
- **1461 — active-list freshness:** `session.active_list` uses durable activity/newest-message information rather than only the session start, so a working turn in an old chat can report fresh activity.
- **1462 — gateway outcomes:** a recent active-turn token belonging to the session and a running gateway makes outcome derivation read `running`, including the gateway's Desktop `local` platform. A cleared, old, other-session or dead-gateway token does not establish liveness.
- **1467 — WAL guard:** same-thread `SessionDB` finalizers may re-enter the process-wide descriptor guard while another handle is being held or released. Other threads remain excluded.

## How it works

### 1429 (`e320c9d273`): routing and notification ownership

`restore_route(model, config, policy_config=None)` decodes a JSON config if needed. If `model_routing.selected_model` differs from the row, the recorded nonempty model wins. Exact pins still require a concrete model/provider tuple and pass `stale_managed_pin(..., policy_config)`; class allocations restore `class:<requested_class>` with provider `auto`. This does not authorize an obsolete pin or silently fail it over.

`kanban_unloaded.build_failed` requires all of: a dict record, no agent, a truthy agent error, and a set readiness event. `live_session_ids` skips only these records. Built agents and builds still in progress remain loaded and retain their normal notification ownership.

### 1444 (`a14ddb7711`): partial receipt index

`LOCAL_LINEAGE_PREDICATE_SQL` is shared literally between `idx_state_meta_local_lineage` and the reader. It matches `gateway.local_policy.v1:*` and uses `CASE WHEN json_valid(value)` before testing `json_array_length(value, '$.lineage') > 1`. A bound GLOB argument would not prove the partial-index predicate to SQLite. Normal writable schema initialization creates the index; the query still works when the index is absent.

### 1445 (`c4b8e717be`): missed-sleep clock observation

`attachPowerSettle` installs an unref'd 5-second ticker. `observeClock` detects gaps over 30 seconds, calls `wake`, and logs one clock-gap line. Admission and qualifying failure handling also observe the clock, so the first request after a freeze need not wait for the ticker. Wake resets the existing 15-second settle window, with a 10-second spread, and starts a 60-second reconnect grace.

`isConnectFailure` recognizes the listed timeout, remote-down, refused/reset, pipe and socket-hangup messages. `absorbFailure` counts matching background reads while suspended or inside grace. The IPC catch path converts those failures to `PowerSettlingError` before normal failure logging. A later clock observation after grace logs one aggregate count. It does not retry the failed operation itself; the next poll is the retry opportunity.

### 1458 (`f7bc2d30af`) and 1459 (`eb0c756e61`): parent-driven seed

The non-search recursive CTE starts with `(SELECT DISTINCT parent_session_id FROM sessions WHERE parent_session_id IS NOT NULL) roots CROSS JOIN sessions s ON s.id = roots.parent_session_id`, using parent lookup followed by primary-key lookup. Childless rows still get their own activity from the outer query. Search paths retain `sessions s` as the seed source.

1458 also introduced `idx_sessions_list_markers` over archive/hidden and JSON marker expressions. 1459 removes its creation and its count-performance test while retaining the parent-driven seed. The replacement test compares the new query against the old correlated seed on the same read-only fixture. 1459 does not issue `DROP INDEX` against databases that already acquired the earlier index.

### 1461 (`23853747bd`): activity in the batch read

`_store_facts` adds `_sql_session_last_active('s') AS last_active` to its existing session batch query. `active_list` returns the maximum of `ended_at` and this activity, falling back to `started_at` only when neither is present. It keeps `started_at` itself unchanged.

### 1462 (`4f888ab00d`): alternate liveness evidence

`_live_turn` first retains the existing alive interrupted-marker-writer check. Otherwise `_gateway_turn_live` reads matching `gateway_routing` entries with a non-null `active_turn_token`, parses `active_turn_started_at`, and ignores missing/unparseable timestamps or ages over six hours. The gateway home is the routing scope's parent, with the DB home as fallback for an empty scope. `_gateway_running` calls `get_running_pid(home / 'gateway.pid', cleanup_stale=False)`. The check is read-only and wrapped in the existing guarded fallback; it does not repair stale gateway state.

### 1467 (`363316fa64`): reentrant lock

The module-level `_LOCK` becomes `threading.RLock()` rather than `threading.Lock()`. The existing per-inode handle counts and descriptor lock/unlock loops are unchanged. Same-thread garbage collection can therefore close a different `SessionDB` inside either scan without acquiring a non-reentrant lock twice.

## Code

| Patches | Runtime files |
|---|---|
| 1429 | `agent/model_routing_restore.py`; `tui_gateway/kanban_unloaded.py` |
| 1444 | `hermes_state_common.py`; `hermes_state_local.py` |
| 1445 | `apps/desktop/electron/power-settle.ts`; `apps/desktop/electron/main.ts` |
| 1458–1459 | `hermes_state_sessions.py`; addition then removal in `hermes_state_common.py` |
| 1461 | `gateway/session_active_list.py`; `gateway/session_live_listing.py` |
| 1462 | `hermes_state_outcome.py` |
| 1467 | `hermes_state_lockguard.py` |

## Tests

These are assertions read from the source tests carried in the patches, **not tests rerun by this documentation mirror**. No live Desktop, routing, sleep/wake or gateway deployment verification is claimed here.

- **1429:** `tests/agent/test_model_routing_row_route_desync.py` covers conflicting-pin reproduction, recorded exact-pin restoration without reallocation, class-route restoration from JSON, policy rejection remaining effective, and unchanged matching rows. `tests/tui_gateway/test_kanban_unloaded_failed_build.py` distinguishes failed/built/building/agent-present records and runs `sweep_once` to persist the full final-send text in a `kanban_notice`. These are helper/allocator and sweep tests, not a live Desktop resume exercise.
- **1444:** `tests/hermes_state/test_local_lineage_index.py::test_unrelated_writes_do_not_rescan_single_segment_receipts` bounds SQLite progress-handler steps after unrelated writes with large single-segment receipts. `test_index_upgrade_and_receipt_mutations_preserve_projection` covers reopen index creation, malformed/odd receipts, valid lineage, update/delete rollback and reading after index removal.
- **1445:** `apps/desktop/electron/power-settle.test.ts` covers ticker-gap wake, admission detecting a gap, regular ticks, connection classification, failure on thaw, suspended failure, reconnect grace/summary, exclusions for awake-after-grace/foreground/POST/server errors, and unref'd ticker wiring. Main-process call ordering is checked by source-text assertions, not mounted Electron IPC. Physical sleep/wake and real SSH reconnection are untested here.
- **1458–1459:** final `tests/hermes_state/test_session_list_json_work.py::test_recent_chain_seed_does_not_parse_childless_rows` counts JSON-validity calls and checks recent ordering. `test_parent_seed_preserves_listing` compares old/new seeds across subagent, child, archived, pinned, source, message-count, ID/title search and local-created-source scopes, including malformed/scalar/BLOB configs in a read-only store. The removed marker-index/count test is not evidence for the final state.
- **1461:** `tests/gateway/test_authority_active_list.py::test_a_running_turn_reports_its_last_activity_not_the_session_start` submits a turn on an artificially old session, writes a durable heartbeat and asserts `working`, unchanged old start time and fresh `last_active`. The new-message-only and ended-at precedence branches are not separately exercised by this added test; Desktop stripe rendering is not exercised.
- **1462:** additions to `tests/hermes_state/test_session_outcome.py` cover refreshed `running` from a gateway token, scope-to-home lookup, `ignore_live_turn`, dead gateway, cleared/stale/other-chat token, and PID lookup with `cleanup_stale=False`. Gateway process status is mocked; no live messaging or Desktop local-platform turn is executed here. Malformed timestamp/error fallback are not separately tested in these additions.
- **1467:** `tests/hermes_state/test_lockguard_reentrancy.py::test_session_finalizer_inside_guard_fd_scan`, Linux-parametrized for `hold` and `release`, forces actual `SessionDB` garbage collection inside descriptor scans in a timeout-bounded subprocess, then checks drained handle counts. `test_guard_still_excludes_other_threads` checks exclusion while held and acquisition after release. Other-platform finalizer paths are not covered by the Linux regression.

## Applying

Use the repository's current [core applying recipe](README.md#applying), preserving published numeric order and any required replay bridges. These are follow-ups to existing fork runtime infrastructure, not a standalone patch bundle; this documentation task did not replay them.

Apply 1458 and then 1459 together to get the documented final listing implementation. 1444's lineage partial index is separate and remains present. 1429 requires managed routing and unloaded-notification infrastructure; 1445 requires the existing power-settle gate; 1461 requires the authority live-listing batch reader; 1462 requires routing tokens and outcome derivation; 1467 requires the existing WAL descriptor guard. Desktop main-process edits require rebuilding/restarting the app through its normal update path, not a disk-plugin reload.

## Gotchas

- Recorded routing intent wins over a disagreeing row, but never overrides current managed policy. A missing/invalid recorded selection does not become a new authorized route.
- Only a completed failed build without an agent is swept as unloaded. This is not a general rule that every in-memory error record is safe to take over.
- The sleep detector is a wall-clock heuristic: a long process pause or forward clock jump can also look like sleep. The grace suppresses matching background connection failures, not successful work or arbitrary errors; callers still receive a rejection.
- Parent-driven listing avoids redundant seed work, not all JSON parsing or all whole-table work. Search deliberately retains a broader seed. Do not describe the reverted marker index as a shipped optimization.
- Gateway liveness requires both token freshness and process evidence. The six-hour guard can stop treating a genuinely longer turn as live; timestamps in the future are not explicitly rejected by this patch.
- A fresh backend activity value alone is not proof of a live Desktop stripe update. WAL reentrancy likewise does not remove inter-thread locking or change inode lifetime accounting.

## Plugin?

No. These changes sit inside persisted route restoration, notification ownership, SQL schema/query construction, Electron IPC/power handling, gateway authority listing, outcome derivation and process-wide SQLite descriptor lifetime management. A plugin cannot reliably replace these internal decisions. There are no new user-facing configuration keys in this set.
