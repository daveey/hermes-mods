# Wake-aware leases and sync stall clocks

Mirror entries: 4455, 4456, 4458, 4485.

## Behavior

- **4455** — New lease waiters on macOS honor the remaining host-wide 90-second wake grace, even without a pre-suspend process sample.
- **4456** — The macOS state CI import surface includes the native wake helper and its platform package.
- **4458** — Replica-staleness observations exclude sampled not-fully-awake intervals, including macOS maintenance wakes.
- **4485** — Outbox warning and incident entrypoints share the fully-awake policy, persist excluded seconds, and reuse one sample with the replica check.

## How it works

`hermes_platform/wake_clock.py` reads `kern.wake_abs_time` and converts Mach ticks with the native timebase into monotonic seconds. Invalid native sizes, zero ticks, unavailable APIs and invalid timebases return no signal. `hermes_state_wake_grace.py` accepts a nonfuture recent native instant or its existing process-local suspend observation; polling does not restart the native grace.

Sync alarms solve a different clock problem: monotonic time can advance during DarkWake without normal sync opportunity. A false `host_fully_awake` sample shifts the observed start by the elapsed interval and accumulates excluded seconds. Replica and outbox memo records carry this across same-boot restarts; legacy records acquire default fields. Outbox warning timestamps remain absolute, not relative to a shifted start.

## Code

- `hermes_cli/kanban_sync_outbox_age.py`
- `hermes_cli/kanban_sync_outbox_observation.py`
- `hermes_cli/kanban_sync_replica_age.py`
- `hermes_platform/wake_clock.py`
- `hermes_state_wake_grace.py`
- `scripts/ci/classify_changes.py`

## Tests

Source regressions cover native conversion/ABI failures, fresh-process lease contention and renewal, grace boundaries and fallback detection. Sync fixtures cover mixed awake/DarkWake sequences, restart persistence, legacy records, once-per-call sampling, failing probes and alternating outbox entrypoints. Native macOS execution and natural wake behavior are not established by this documentation pass.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/hermes_cli/test_kanban_sync_outbox_awake.py`
- `tests/hermes_cli/test_kanban_sync_outbox_observation.py`
- `tests/hermes_cli/test_kanban_sync_push_starvation.py`
- `tests/hermes_cli/test_kanban_sync_replica_age.py`
- `tests/hermes_platform/test_wake_clock.py`
- `tests/hermes_state/test_fresh_process_wake_grace.py`
- `tests/hermes_state/test_session_turn_lease_wake_grace.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

The sync probe deliberately also excludes some idle lid-closed external-display and idle battery-powered states until activity resumes. Probe failure counts as awake, so broken probing cannot hide a real stall; non-macOS probing does not require a macOS subprocess. Native lease grace and fully-awake alarm exclusion are separate mechanisms, not interchangeable sleep clocks.
