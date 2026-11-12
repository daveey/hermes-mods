# Hub CPU advisory sampled on its own thread, with a stale-advisory card

Patches: **2507**.

## Behavior

With the root `kanban.admission.cpu_busy_below_pct` set, every claim reads the advisory sample file (`cache/hub-cpu-sample.json`) and defers as `hub_cpu_unknown` when it is older than 15 s. The earlier [split-phase sampler](split-phase-cpu-sampling-2409b.md) took snapshot A after one sync round and B after the next; a hub round of 3–20 s always pushed the pair outside the 1000–2000 ms window check, every sample was dropped and the hub claimed nothing with free slots. Now a dedicated thread takes the full sample, so slow sync rounds can neither void nor delay it.

- A `hub-cpu-advisory-sampler` daemon thread takes a full ~1 s `cpu.sample` every 5 s while the root limit is on and writes each valid one to the advisory file. Gate off or unknown: it samples nothing and forgets the last sample.
- The metrics report no longer measures; it takes the sampler's newest sample at most once (never replayed), only if measured under the same limit and at most 10 s old.
- Detection: once per dispatcher tick, if the advisory has been unusable (older than 15 s, missing or malformed) for 60 s of the process's own observation, one card (idempotency key `hub-cpu-advisory-stale`) is opened on the default board naming the sample age and the sampler counters. A fresh sample ends the incident; a later incident comments on the open card (hourly at most) or opens a new card once that one is done.

## How it works

`hermes_cli/machine_cpu_sampler.py`: `AdvisorySampler` (`run_once`, `take`, `start`, `close`) plus `status()` counters (drops, consecutive drops, records). `_run_forever` in `kanban_sync_client.py` creates it, sets `metrics.cpu_take_fn = sampler.take`, starts it and closes it in a `finally`, so a stopped loop lets the advisory go stale (fail-closed, see [kanban-hub-cpu-cap-gate.md](kanban-hub-cpu-cap-gate.md)). The pending-wake clamp in the loop and the unused begin/finish primitives in `machine_cpu_metrics.py` and `machine_metrics.py` are removed. `hermes_cli/kanban_hub_cpu_alert.py` `check` is called from the gateway dispatcher loop in `gateway/kanban_watchers.py`; it never raises. `kanban_admission.hub_cpu_advisory` and `docs/worker-first-v1.md` now name the sampler as the feed.

## Code

`gateway/kanban_watchers.py`, `hermes_cli/kanban_admission.py`, `hermes_cli/kanban_hub_cpu_alert.py` (new), `hermes_cli/kanban_sync_client.py`, `hermes_cli/machine_cpu_metrics.py`, `hermes_cli/machine_cpu_sampler.py` (new), `hermes_cli/machine_metrics.py`, `docs/worker-first-v1.md`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_machine_cpu_sampler.py` (sampler cadence, at-most-once `take`, limit/age refusal, drop counting, gate-off, thread close), `tests/hermes_cli/test_kanban_sync_slow_round_cpu.py` (the real `_run_forever` loop with slow rounds: advisory written during each round, fresh at claim time, sampler stopped when the loop returns), `tests/hermes_cli/test_kanban_hub_cpu_alert.py` (the `check`/`report` card logic), plus updated reporter, timing and gate-budget tests. The call from the live dispatcher tick in `kanban_watchers.py` is not exercised by these tests directly (untested).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Replaces the begin/finish split-phase tests and primitives; code importing `cpu.begin`/`cpu.finish` must move to `cpu.sample`.
- The sampler runs only where the sync loop runs; elsewhere the advisory still goes stale by design.
- The card is filed on the default board and assigned to `default`.

## Plugin

Core change; no plugin required.
