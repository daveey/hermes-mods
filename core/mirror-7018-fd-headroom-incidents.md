# File-descriptor headroom guard and incident reporting

Patch coverage: 6677, 6678, 6679, 6680, 6681, 6682, 6683, 6698, 6744, 6751, 6794, 6795, 6932, 6933, 6934, 6935, 6936.

## Behavior

Servers now watch their own file-descriptor usage against the process limit and file a durable incident when headroom is running out.

- **Config** (`runtime.fd_headroom`): `enabled` (default on), `warn_ratio`, `raise_ratio`, `horizon_hours`, `interval_seconds` (≥30), `max_soft` (≥1024). Invalid values fall back to defaults with a `config_error`; ratios must satisfy 0 < warn < raise < 1.
- **Warning** at `warn_ratio`, or when growth over the last 30 minutes (≥4 samples spanning ≥20 minutes) projects exhaustion within the horizon; low-pressure predictions are suppressed. Warnings log process count, limits, loaded core and a bounded census (resource-leases, sqlite, socket, pipe, other; stops at 2,000 descriptors or 0.5 s and reports partial coverage).
- **Rescue** at `raise_ratio`: raises only this process's soft limit to min(hard, `max_soft`, 4× soft) after a readback confirms it; denied/capped changes keep monitoring in state `capped`. It never changes the configured soft-limit setting, the hard limit, other processes or service configuration.
- **Incident card**: one portable card per host/profile/role on the default board with at most one comment per hour, retried each tick under the same durable key; failed reads never open a new incident; delivery errors are logged hourly and surface in runtime status/health. Terminal courier receipts (rejected/failed/expired) latch and are rearmed only by the *same producer's* healthy crossing (per-process birth tokens, bounded set of 64), including after restart; healthy samples keep terminal history quiet; transient incident-storage diagnostics clear on a later successful save.
- Wire identity: incident ids are encoded to the courier's raw ASCII request-id grammar (the original form with ISO `+`/encoded colons produced a generic 404); persisted pre-upgrade identities are preserved.
- Board evidence is mapped at the transport boundary (live/deleted/never_existed/unknown); inconclusive acknowledged incidents are retained, explicit deletion closes. Probe cards are non-dispatchable. Startup samples before the runtime reservation; telemetry is published only after the launch home is owned.
- Checks run off-loop, one worker at a time, skipped overlaps are counted.

## How it works

`hermes_cli/resource_limits.py` (`check_fd_headroom`, `_fd_snapshot`, `fd_headroom_status`, `request_fd_headroom`, soft-limit raise), `fd_headroom_config.py` (`validated`), `fd_headroom_incident.py` (durable intent saved *before* delivery, `report`, `failure_note`, producer birth tokens), `fd_headroom_transport.py` (`Client.create/comment/read`, wire envelope), `gateway/run_housekeeping_resources.py` (cadence/ticks), `gateway/run.py`, `run_bootstrap.py`, `status.py`.

## Code

The files above plus `hermes_cli/config_defaults.py`, `hermes_cli/web_server.py`, `hermes_cli/main.py`, `website/docs/user-guide/configuration.md`.

## Tests

Read from source; **not executed here**. `tests/**/test_fd_headroom*.py` (amended, birth_identity, callers, census, delivery, incident), `test_plugin_check_housekeeping.py`; later commits pin persisted wire identity and crash-safe intent. Live Board delivery and real descriptor exhaustion are **untested** here; commit text mentions live-hub acceptance probes as owner observations only.

## Applying

Apply in ordinal order (a long fix chain: 6680 supersedes the first implementation in 6677).

## Gotchas

- The rescue only affects this process; do not treat it as a replacement for the configured limits.
- Producer-qualified rearm: a healthy sibling must not rearm the leaking producer.

## Why core, not a plugin?

Gateway/runtime resource handling and the incident courier are core.
