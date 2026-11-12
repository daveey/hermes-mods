# CPU metrics survive sampler gaps and root lease takeover

Patches: **2880**.

## Behavior

While the root CPU gate is on, the anchored metrics sender waits briefly for a fresh sampler window and avoids erasing a recent CPU triple with a CPU-less report. A contended root gateway retries its posted lease claim promptly after a restart.

## How it works

**2880:** `AdvisorySampler.wait_fresh` uses a condition notified on publication. Sender ticks wait at most 1.5 seconds off the sync loop, withhold CPU-less reports until the 30-second keepalive bound, and count/log withheld reports separately. Suppressed reports are not report starts. A late send shifts the next anchor enough to preserve half-period spacing rather than skipping it. Loop takeover while ON uses the sender’s bounded transport; stall warnings say whether a tick still holds the Reporter. `SingletonLease.retry_interval` caps a contended root’s retry at two seconds, leaving other states/profile gateways on their configured interval.

## Code

- `gateway/kanban_singleton.py`
- `gateway/kanban_watchers.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/machine_cpu_sampler.py`
- `hermes_cli/machine_metrics.py`
- `hermes_cli/machine_metrics_sender.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_machine_metrics_sender_stalls.py` checks fresh-window wait/bounds, keepalive/suppression, cadence, bounded takeover, in-flight warning, condition wakeup, root gate scope and logs. Existing sender tests adapt spacing expectations. `test_kanban_singleton_lease.py` checks root/profile retry intervals and the acquisition sleep loop.

- `tests/gateway/test_kanban_singleton_lease.py`
- `tests/hermes_cli/test_machine_metrics_sender.py`
- `tests/hermes_cli/test_machine_metrics_sender_stalls.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

No CPU sample is replayed as fresh. A stalled tick holding the Reporter lock still prevents loop reporting; the corrected warning does not promise otherwise. Gate-off and non-Linux paths retain normal reporting. Benchmarks/live incidents in commit prose were not repeated here.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
