# Split-phase registry CPU sampling and drop diagnostic

Patches: **2408–2409**.

## Behavior

With the installation-root CPU gate ON, the reporter takes the first CPU snapshot without waiting, holds it pending, and finishes it on the same thread after the one-second window; the sync wait is clamped only while a snapshot is pending, leaving polling, the 0.5 s outbox tick, ten-second report cadence and locked admission sample unchanged. OFF keeps the original single-phase report. Dropped samples (late/invalid window, changed root limit) are counted and logged as `machine metrics admission quality: cpu_sample_drops=N` (cumulative per reporter instance, before transport).

## How it works

`machine_cpu_metrics.begin/finish` replace the blocking `cpu_sample_fn` seam; wake and finish share `WINDOW_S`. A finish exception never leaves a due pending snapshot; unsupported platforms use a 600 s retry and no invented CPU value.

## Code

`hermes_cli/machine_cpu_metrics.py`, `machine_metrics.py`, `kanban_sync_client.py`; note `docs/split-phase-cpu-reporting.md`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_machine_cpu_split_primitives.py`, `test_machine_metrics_split_cpu.py`, `test_machine_metrics_split_finish.py`, `test_machine_metrics_drop_quality.py`, `test_kanban_sync_split_cpu_loop.py`, `test_hub_cpu_gate_budget.py`, plus updated sync timing/connection tests. Live latency acceptance is not covered.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

The drop count is not all lost reports (exceptions before the increment and post-sample send failures are excluded). Non-Linux hosts are not natively verified.

## Plugin

Core change; no plugin required.
