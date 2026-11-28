# Hot-path read-cost reductions (SQLite hand-offs, request coalescing, memoization)

Patch coverage: 6619, 6620, 6621, 6622, 6623, 6629, 6634, 6637, 6640, 6641, 6653, 6654, 6655, 6673, 6696, 6697, 6712, 6730, 6745, 6746, 6747, 6748, 6754, 6755, 6947, 6955, 7015.

## Behavior

A set of read-path optimizations that aim to keep behavior while cutting statements, GIL hand-offs and duplicate work on busy serve/Desktop backends. They are not claimed policy-free beyond what the cited tests pin. Most items add a source test that counts statements, calls or handles; the ledger regeneration (6629), the import-wiring cleanup (6623) and the selector-parity fixture edit (6655) carry no new behavioral test of their own, and 6640 is covered by a TypeScript hook test rather than a statement count.

- Throughput metrics reuse safe readers across refreshes; actionability schema discovery is bounded to one handoff instead of one row per name.
- Overlapping identical task-detail reads are coalesced into one flight; pending capture demand is coalesced.
- The done-window selector asks for completed-desc order explicitly (skips a discarded dispatch sort); bounded complete SQL safety parses are cached (≤4096-char exact built-in strings, ≤256 entries, refusals not cached).
- Desktop does one session-list read per change tick, not two.
- Same-owner origin obligations are read in one batched pass; upstream annotation reads are shared across local obligations; placeholder policy is snapshotted once per read; neighbour graph expansion no longer re-reads unchanged parents' full event histories.
- Session/projects tree: one board external read per response and reused idle turn-lease reads; idle stores' rows are kept until they commit, with one goal read per response, keyed on the full automated-source policy including interactive sources.
- Lanes flow decodes only flow-relevant event evidence (SQL substring superset, then exact scan).
- Loaded-chat notification walks are bounded to pending candidates; TUI private signature reads fetch only the asking chat, then hand subscription rows and column probes over in one row.
- Retained-result inventory opens one lineage handle per pass.
- Todo dependency waits and terminal-parent restriction reads are batched on the wait-row poll path (`parent_states_many`, `parent_blocks_many`).
- Replica payload string runs are skipped with possessive C-regex runs instead of per-escape Python work.
- Nested `sync.detail` timing marks (7015) are diagnostic by intent (owner-reported "no behaviour change"), but the production edit is not purely additive: connection-open code now routes the WAL, lock-guard and pragma steps through `kanban_sync_cpu.mark` wrappers (the PRAGMAs moved verbatim, in the same order, into `_configure_pragmas`), dispatch-owner lifecycle/tick locks enter via `marked_cm`, and an unknown mark part raises `ValueError` when called inside a sync step. Outside a sync step `mark` is a plain call.

## How it works

Each item replaces repeated per-row or per-card SQLite statements with one batched or memoized read, or coalesces identical in-flight requests, keeping the original semantics and bounds.

## Code

`machine_throughput_metrics.py`, `kanban_db_parent_states.py`, `kanban_origin_ledger.py`, `kanban_origin_local.py`, `kanban_origin_obligations.py`, `kanban_external_activity.py`, `turn_session_activity.py`, `goal_session_activity.py`, `web_routers/profiles.py`, `tui_gateway/methods_projects.py`, `tui_gateway/notification_idle.py`, `session_notifications.py`, `tools/process_registry_results.py`, `kanban_lanes_flow_evidence.py`, `kanban_replica_transport.py`, `kanban_sync_cpu.py`, `plugins/kanban/dashboard/task_detail_flight.py` (`mount`, `get_task_endpoint`), `plugin_api.py`, `board_read.py`, `board_incremental.py`, `kanban_replace_guard.py`, `kanban_completion_refs.py`, `kanban_dependency_wait_clock.py`, `kanban_containment_terminal.py`, `sessions_native_source.py`, `kanban_db_connect.py`, `kanban_dispatch_owner.py`, `kanban_generation.py`, `kanban_sync_board_watch.py`, `kanban_sync_outbox_age.py`, `session_throughput_revision.py`, `kanban_actionability_sources.py`, `profile_session_scan_cache.py`, `apps/desktop/src/app/contrib/hooks/use-background-sync.ts`, `serve_handler_ledger.json` (regenerated for the new route).

## Tests

Read from source; **not executed here**. Named suites include `test_actionability_schema_work.py`, `test_goal_session_activity.py`, `test_kanban_dependency_wait_clock.py`, `test_kanban_completion_refs_columns.py`, the wait-row statement-count test and `use-background-sync.test.ts`. Statement/CPU figures in commit text come from specific deployments and are not reproduced. The task-detail flight route is the only new route path; its live behavior is **untested** here.

## Applying

Apply in ordinal order; the pairs 6653→6654→6655, 6746→6747 and 6754→6755 are dependent (6827 belongs to the annotation-holder series, not this one).

## Gotchas

- Parse-cache refusals must never be cached; cache bounds are part of the contract.
- Several optimizations rely on keying by the *full* policy tuple.

## Why core, not a plugin?

All are core store/gateway/dashboard internals.
