# Shared facts core: change feed, immutable fact store, projections and the loaded-notification consumer

Mirror snapshot 5923; ordinal 5878.

Entries:

- **5878**: Add shared facts core and activate bounded loaded notifications

Follow-ups that extend this entry are documented separately: [demand channel and replica transport](mirror-5923-b-fact-demand-replica-transport.md), [loaded notification scheduling](mirror-5923-b-loaded-notification-fact-schedule.md), [in-process links display](mirror-5923-b-links-facts-startup.md).

## Behavior

- Adds the "shared facts" layer: a single owner captures task, session and annotation inputs once, publishes them as immutable generations, and consumers read those generations instead of each re-reading the databases on request paths. Almost all of it ships **inert**: the integrated fact sources and consumer adapters (board, sessions, projects, annotation views, remote links) exist, are fixture-tested, and are not wired to production reads by this entry.
- The only default-on activation is `display.loaded_notification_f` (default `true`, documented in the configuration guide). With it ON, loaded-session Kanban notification pollers no longer own one OS thread per chat: one shared cooperative executor advances every loaded session's poller, one bounded reconciler (30 s process clock) repairs missed wakes and keyed wake batches name the replica rows that moved. Durable claims, transcript ACK retries and delivery owners are unchanged. Setting it to `false` restores the legacy per-chat polling for newly initialized loaded sessions; it does not disable delivery.
- Other pieces added without activation: bounded task/session content capture, paged bootstrap with closed dirty replay and revision recovery, validity-expiry deadlines, a local socket host with scoped authenticated IPC and an in-process transport with per-consumer lifecycle control, native goal/cron annotation membership sources, a remote-links display source, and pure canonical projections for sessions/sidebar/board/projects.
- A per-session message/counter content-change clock (`hermes_state_session_content_change.py`, maintained by triggers in the writer's transaction) supplements the listing cursor so edits, deletes and moves are visible without copying message bodies.
- Several design notes ship under `docs/designs/` (annotation contract, board contract, implementation checkpoint, keyed notification contract). They are design records, not delivery claims.

## How it works

- Contract and store: `hermes_cli/fact_contract.py` (immutable values, health states `fresh`/`stale`/`unknown`), `fact_store.py` (atomic immutable generations, subscriptions, publication condition), `fact_wire.py` (private JSON transport, never pickle) and `fact_ipc.py` (length-bounded socket framing).
- Ownership and scheduling: `fact_feed.py` is the single-owner event/retry scheduler (no polling: bootstrap, native watch hint, explicit source deadline or failed-capture retry); `fact_source_commit.py`, `fact_source_signal.py`, `fact_source_watch.py`, `fact_task_source.py`, `fact_session_source.py`, `fact_content_owner.py`, `fact_content_rows.py`, `fact_capture.py` capture guarded, bounded content (id batches of at most 500; large captures use paged bootstrap and are never published as a prefix); `fact_expiry.py` publishes owner deadlines so readers never read clocks.
- Hosting and transport: `fact_host.py` (single-process owner with startup-authorized scoped IPC), `fact_inprocess.py` (authorized scopes, demand queue, delivery and expiry workers, `IncrementalLifecycle` per consumer), `fact_replica.py`, `fact_runtime.py`, `fact_demand.py`, `fact_subscription.py`, `projection_request.py` (request-local binding custody).
- Projections: `fact_materializers.py`, `fact_reducers.py`, `sessions_projection*.py`, `fact_annotation_*`, `kanban_annotation_*`, `plugins/kanban/dashboard/board_*` (pure `project_captured` board projection reused by the legacy path), `projects_fact_routes.py`, `sessions_fact_routes.py`.
- Notifications: `tui_gateway/notification_facts.py` (fact wake gates), `notification_loaded_f.py`, `notification_loaded_executor.py`, `notification_loaded_local.py`, `session_notifications_loop.py` (pollers as generators driven either on the session thread, OFF, or by the shared executor, ON), `kanban_remote_notices_keyed.py`, `kanban_unloaded*.py`, `notice_fanout*.py`, `kanban_claim_custody.py`, `gateway/kanban_notification_schedule.py`, `gateway/kanban_remote_owner.py`.

## Code

Shipped non-test paths: a large set (about 100 production files). Principal groups:

- `hermes_cli/fact_*.py` (contract, store, wire, ipc, feed, host, inprocess, replica, runtime, demand, capture, expiry, sources, reducers, materializers, parity, plugin mount, remote links, annotation membership/views, pages, map, bootstrap, fake test doubles)
- `hermes_cli/sessions_projection*.py`, `projection_request.py`, `projects_fact_routes.py`, `sessions_fact_routes.py`, `kanban_annotation_*.py`, `kanban_activity_projection.py`, `kanban_links_refresh.py`, `kanban_task_change.py`, `kanban_db_connect.py`, `kanban_session_activity.py`
- per-source session activity helpers (`cron_`, `goal_`, `process_`, `review_`, `subagent_`, `turn_session_activity.py`)
- `hermes_state_schema.py`, `hermes_state_write.py`, `hermes_state_session_content_change.py`
- `hermes_cli/config_defaults.py`, `hermes_cli/backup_restore.py`, `hermes_cli/web_server_app.py`, `hermes_cli/web_server_dashboard.py`, `hermes_cli/web_routers/profiles.py`
- `plugins/kanban/dashboard/board_*.py`, `plugin_api.py`
- `gateway/kanban_notification_schedule.py`, `gateway/kanban_remote_owner.py`, `gateway/kanban_watchers.py`, `gateway/kanban_watchers_notifier.py`
- `tui_gateway/` notification, notice fan-out, unloaded and consumer-health modules, `methods_config.py`
- `docs/designs/shared-facts-*.md`, `scripts/boundary_lint_baseline.json`, `scripts/boundary_lint_changefeed.md`, `website/docs/user-guide/configuration.md`

## Tests

Source-side tests shipped by this entry (roughly 90 test files under `tests/hermes_cli`, `tests/hermes_state`, `tests/plugins`, `tests/gateway`, `tests/tui_gateway` and `tests/agent`); none were executed by this documentation pass, so no pass result is claimed here. Families:

- fact contract/store/wire/host/inprocess/runtime/lifecycle/restore/parity/provenance/publication-clock tests (`test_fact_*.py`)
- capture, content owner/rows/demand, source commit/signal, task and session source/bootstrap/replay, large-task tests
- annotation tests (`test_annotation_*.py`, `test_kanban_annotation_facts*.py`) and real-host parity processes
- sessions/projects projections and routes, board fact views/capture/legacy/local/replica tests
- notification tests under `tests/tui_gateway/` (`test_notification_fact_loop.py`, `test_loaded_notification_f.py`, `test_kanban_unloaded_*.py`, `test_kanban_remote_*.py`, `test_notice_fanout_*.py`, `test_notification_ack_retry.py`, `test_notification_real_host.py`) and `tests/gateway/test_kanban_notification_schedule.py`, `test_kanban_notifier_*`, `test_kanban_remote_owner_custody.py`
- `tests/hermes_state/test_session_content_change.py`, `test_get_messages_include_compacted.py`, `tests/agent/test_pre_compress_checkpoint_contract.py`, `tests/hermes_cli/serve_handler_ledger.json`

## Applying

This entry is part of the ordered snapshot 5923. Apply it only through the [segmented Applying recipe in core/README.md](README.md#applying) (`git am -3 --empty=keep` over the full ordered list, plus any named repair overlay). It is not an independent patch.

## Gotchas

- Inert is deliberate: sources/adapters that are not mounted must not be treated as production coverage; per-consumer switches and the in-process lifecycle admit consumers one at a time.
- Rollback for notifications is `display.loaded_notification_f: false`, effective for newly initialized loaded sessions.
- Fact readers never discover profiles, spawn owners or read clocks on request paths; authorized scopes come from startup.
- A reset or incomplete capture means "catch up", never "seed at the fact revision".
- The checkpoint documents under `docs/designs/` record intentions and open items; they are not claims of delivery.

## Plugin

Core change with a Kanban dashboard plugin-side projection (`plugins/kanban/dashboard/board_*.py`, in-tree); no external plugin or hook is installed by it.
