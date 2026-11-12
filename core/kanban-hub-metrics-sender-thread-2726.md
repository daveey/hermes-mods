# Hub metrics reports on an anchored sender thread

Patches: **2718**.

## Behavior

- While the installation-root CPU gate (`kanban.admission.cpu_busy_below_pct`) is on, the hub's machine-metrics report carries the measured CPU triple that the Board's hub-overflow admission reads. These reports now go out on their own daemon thread named `metrics-sender`, on a fixed schedule, instead of once per sync-loop iteration after `sync_all_once` and `reconcile_links`. Before, a long sync round held every report, the loop wait ignored the report's due time, and a late call pushed the next one later, so a read-only probe saw 15 to 20 CPU-bearing reports per 300 s (30 expected at the 10 s period) with stalls of 18 to 39 s.
- Tick `k` is due at `t0 + k * REPORT_EVERY_S` (10 s, monotonic). An overrun skips the anchors it covered; there is no burst.
- Every send from this thread, with or without the CPU triple and including 400 compatibility retries, is one bounded call: a private connection, a 2 s wall budget (`CPU_REPORT_BUDGET_S`), never resent. A report with retries is at most 3 x 2 s; an ordinary one is a single send. The loop's kept-alive connection (30 s timeout) is not used by the sender.
- The loop still calls its report check each iteration. While the sender is alive, saw the gate ON at its latest tick and finished that tick within 3 periods, the check returns False without collecting or sending. With the gate off, off Linux, or with a dead or stalled sender, it is exactly the old `Reporter.report_due`, so worker hosts and gate-off hubs behave as before. When the loop takes reporting back from a stalled sender it logs one WARNING.
- Logging: one INFO line per attempted send (`metrics_sender send t0= t1= end_ms= win_ms= out= written= run_ns= wait_ns= worker_run_ns= worker_wait_ns=`: monotonic times, the CPU window, outcome, whether request bytes reached the socket, and scheduler run/wait time of the sender thread and of the send's worker), and `metrics_sender ticks=N sent=N failed=N skipped=N` at most every 60 s and at close.
- Not started on non-Linux. No exception ends the thread. Closing joins for at most 2.5 s, even with a send in flight.

## How it works

New `hermes_cli/machine_metrics_sender.py` defines `MetricsSender` and `board_post(ident)`. The sync loop (`_run_forever` in `kanban_sync_client.py`) creates and starts it beside the CPU advisory sampler, calls `sync_timing.report(sender.loop_report)` instead of `metrics.report_due`, and closes it in the same `finally`. The thread runs in a copy of the loop thread's contextvars.

`Reporter` in `machine_metrics.py` gets a non-blocking lock and a `started_at` stamp shared by both paths. `report_due` returns False if the sender is mid-report. The new `report_tick(send)` runs the unchanged report body through the sender's transport and uses a different due rule: the tick's anchor is the due time, guarded only by another report in flight, a report start on either path less than `REPORT_EVERY_S / 2` ago, and the Board's 404/405 backoff. It does not reuse `next_at`, so a tick that wakes earlier relative to its anchor than the previous one is not dropped. `loop_report` reads in-memory state only (no config read, no Reporter lock).

`kanban_sync_client._bounded_call` takes an optional `probe` dict. `DeadlineRequests` moved into the new `hermes_cli/kanban_sync_bounded_probe.py`, together with `watch_written` and `schedstat`, to keep the client file under its line cap.

## Code

`hermes_cli/machine_metrics_sender.py` (new), `hermes_cli/kanban_sync_bounded_probe.py` (new), `hermes_cli/machine_metrics.py`, `hermes_cli/kanban_sync_client.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_machine_metrics_sender.py` (23 tests, new) and `tests/hermes_cli/test_kanban_sync_timing_loop.py` (its gate-ON real-loop instrumentation test now disables the sender start so it still exercises the loop's own report path). Entry path: `MetricsSender`, `Reporter.report_tick` / `report_due` and the real `_run_forever` loop with a fake clock and stub transports (a blocked round still yields sends from the sender only, no drift over jittered ticks, out-of-order wakes, overrun skip, gate ON/OFF/ON, sender death and stall hand-over, thread survives raising collect/send/tick, close timing, thread name, log cadence, per-send line including a 400 retry, a triple-less send against a hung Board cut at 2 s, delegated versus full records, non-Linux). A real Board and real production CPU load are **untested**. The commit message reports that 14 of 14 source mutants were killed and that a live probe showed the undercount; neither was reproduced in this mirror.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Only active while the CPU gate is on; otherwise the loop path is unchanged.
- The sender's bounded call is never resent, so a report that times out at 2 s is lost for that tick.
- `kanban_sync_client.py` no longer defines `DeadlineRequests`; it re-imports it as `_DeadlineRequests`.

## Plugin

Core change; no plugin required.
