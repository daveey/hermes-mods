# Per-pass CPU accounting for loops and polled routes

Mirror ordinals: 3781.

## Behavior

- Named loop/route passes expose cumulative runs, skips, changes, fallbacks, rows, errors, calling-thread CPU and wall time.
- Long-lived gateway/serve/TUI processes can periodically publish per-PID JSON snapshots with process/thread CPU and attributed/unattributed totals.
- Instrumentation adds evidence about polling costs; it does not itself reduce polling frequency or establish idle CPU acceptance.

## How it works

`loop_accounting.Pass`, `timed` and `accounted` use calling-thread CPU time and monotonic wall time. Nested names retain inclusive individual counters, but only the outermost pass contributes to per-thread accounted CPU. Same-thread spans bridge a host-loop lease acquisition and its beat. Names are bounded with overflow folding; snapshots fold departed thread totals, read Linux thread procfs where available, and leave unavailable per-thread CPU fields null elsewhere.

Snapshots are written atomically beneath the root's run/loop-accounting directory. A default-enabled writer starts for long-lived process kinds, reads its configured interval and periodically prunes stale dead-PID files; disabling the writer leaves counters available. Call sites wrap dispatcher/board passes, notifier collection, sync hooks, annotation refresh/revalidation, links/index refresh, metric ticks, change watchers, unloaded/producer/notification sweeps, RPC methods and selected REST readers. FastAPI-visible decorated signatures are preserved.

## Code

- `gateway/kanban_watchers_dispatcher.py`
- `gateway/kanban_watchers_notifier.py`
- `hermes_cli/host_loop_lease.py`
- `hermes_cli/kanban_annotation_host.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_links_refresh.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_activity_revalidate.py`
- `hermes_cli/kanban_sync_timing.py`
- `hermes_cli/loop_accounting.py`
- `hermes_cli/machine_metrics_sender.py`
- `hermes_cli/observability/shared_metrics_process.py`
- `hermes_cli/web_routers/profiles.py`
- `hermes_cli/web_routers/sessions.py`
- `plugins/kanban/dashboard/plugin_api.py`
- `scripts/boundary_lint_baseline.json`
- `tui_gateway/change_watcher.py`
- `tui_gateway/kanban_remote_index.py`
- `tui_gateway/kanban_unloaded.py`
- `tui_gateway/producer_admission.py`
- `tui_gateway/rpc_dispatch.py`
- `tui_gateway/session_notifications.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/hermes_cli/test_loop_accounting.py` — Counter/row/error assertions verify outcomes and reraising; real busy/sleep intervals distinguish thread CPU from wall time. Nested/spanning accounting avoids double totals, cross-thread span closure records nothing, host lease beats produce a pass, overflow and no-procfs churn stay bounded, snapshots carry role/PID/counters and leave no temporary file, writer start/stop/disabled behavior and stale dead-PID pruning are tested. Sync hooks assert rows/skips/errors per board.
- `tests/hermes_cli/test_loop_accounting_bench.py` — Pass and decorator microfixtures assert a 20-microsecond per-call budget using repeated best measurements. These are test thresholds, not results obtained here.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

Per-name CPU is inclusive and must not be summed naively across nested names. Snapshot process CPU includes work outside instrumented passes. Linux procfs detail and non-Linux fallbacks differ; no per-route end-to-end REST or live production CPU result is claimed by this document.

## Plugin?

No. The dashboard plugin is one instrumented consumer, but the accounting engine, loop ownership and process writer require core integration.
