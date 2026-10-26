# Runners observe-only inventory

Validation, persistence, pure health rules, bounded GitHub/SSH collection and a
cached authenticated dashboard API. Import and `register(ctx)` remain inert:
collection starts only through the explicit supervised entry script. Mutation,
dispatch, repair sessions and workspace execution must remain disabled.

## Collector and API deployment

Install PyYAML and `jsonschema[format]` into a dedicated production interpreter.
Create a private mode-0600 runtime JSON with `credentials` mapping opaque refs to
`{scope, env_var, key}` broker references (never token values), and `probes` mapping
opaque SSH refs to `{target, spec}`. Credential aliases sharing a provider quota
must use the same `key`. Targets contain hostname, username, identity_file,
known_hosts_file and port; specs contain platform, opaque-ref-to-exact-service
`services`, build_volume and explicit `tools`. Optional `job_repositories` maps
scope IDs to bounded repository allowlists; omitted means no job enrichment.

Provision a dedicated forced-command SSH identity additively. Install source from
`probes.ssh.render_script` and its interpreter at trusted root-owned non-writable
paths; pin host keys through an independently authenticated route. Restrict keys
with `restrict,command="/usr/bin/python3 -I /trusted/probe.py"`. No PTY, forwarding,
arbitrary shell, environment inspection or credential reads are permitted. Never
reuse an administrative private key as the collector probe identity. Unprovisioned
hosts remain explicitly unavailable; do not infer host health from GitHub status.

Run `scripts/install_service.py --install --python /absolute/python` with required
`--config`, `--runtime`, `--state-dir`, `--owner-gateway-id`, `--owner-profile`, and
`--owner-epoch` arguments. It installs only the user collector unit (systemd or
launchd), not the gateway or runner services. Use `--uninstall` to remove only that
collector. Linux requires an available user manager and linger for logout survival.
The runtime uses an OS lifetime lock, trusted owner arguments, full config fences,
atomic snapshots and durable per-request rate-budget reservations. Config changes
require explicit reconciliation and restart, not silent owner/epoch adoption.

Nominal inventory/host/tool intervals are 30s/60s/15min. Active, discovery and
refresh requests share a persisted fair schedule; quotas can delay freshness.
Coverage never claims complete merely because a tick finished. HTTP/SSH have
bounded subprocess deadlines; partial pagination never replaces last-known rows.
No LLM or notifications run on ticks.

Permission denials (403/404 without quota or secondary-throttle evidence) cool down
only the denied resource. Actual rate limits share the credential quota key.
Cooldown cause and deadline survive collector restart; the API exposes sanitized
error codes and retry delays, and the panel ages the displayed delay from its
observation time. GitHub OS aliases are normalized case-insensitively.

Each runner's provenance records its last actual observation, not the latest
collector attempt. The panel shows this row age separately from the collector
heartbeat. Failed refreshes retain last-known registrations without asserting
fresh readiness or available capacity.

Host provenance uses `source_id=host.resource_id`, including hosts with no bound
registration. Its timestamps come from the actual fixed probe; missing evidence is
unknown, failed probes unavailable, and expired evidence stale. Inspect adds
`job_monitoring` (state/reason_code/observed_at/expires_at) and `observed_jobs` with
bounded provider IDs, status, conclusion, observation times and GitHub links.
These read-only external observations are separate from `jobs`, the dispatched Job
contract: no request/attempt/source identity is fabricated. Rows are filtered by
scope and provider runner ID, capped by history_limit (100), and never fetched on
GET. Empty rows do not imply monitoring is disabled. Older collector snapshots
report unknown configuration; an explicit absent allowlist reports not_configured.
Truncated bounded queries remain configured but identify their partial reason.
Expired job evidence reports unknown/stale_evidence; cooldowns never renew its age.
When any candidate is busy, bounded enrichment queries in-progress runs so newer
queued/completed runs cannot exhaust the budget before active work is inspected.
Recent-only candidates use the bounded unfiltered history query; neither mode
claims exhaustive job history or turns a missing attribution into runner failure.

Dashboard discovery mounts `/api/plugins/runners/v1`: GET `/enrollment`, GET
`/inventory`, GET `/runners/{id}`, POST `/refresh`. Enrollment returns the explicit
profile, enrollment flag and observe capabilities without opening the database or
starting a collector. A profile with no runners configuration is not enrolled;
invalid configuration or an unavailable plugin is not silently treated as empty.
During mixed-version deployment only an enrollment HTTP 404 permits a read-only
legacy inventory probe through the same authenticated route and owner checks.
Validated rows remain visible as stale/deployment-pending with actions and ready
capacity disabled until enrollment works. Authorization errors never fall back;
explicitly unenrolled profiles never query inventory.
Other GETs read SQLite only. Refresh queues bounded,
deduplicated asynchronous work. Core gateway authentication plus profile and
same-origin/CSRF fences apply; no standalone unauthenticated fallback exists.
Config parsing is stat-fenced/cached while response freshness and owner checks run
on each request. Deployment acceptance must separately verify live discovery,
authenticated routing, provider-count parity and measured GET p95; unit tests do
not establish live installation. See `dashboard/README.md` for API details.

## Boundaries

- `models.validate(definition, value)` validates the Phase A JSON Schema with format
  checks and returns detached data. Errors do not echo submitted values.
- `models.runner_id(provider, endpoint, scope_id, provider_runner_id)` supplies a
  deterministic handle for new enrollments. Preserve pre-enrolled IDs/bindings;
  never remint existing IDs or merge registrations by display name.
- `config.loads/normalize` inserts safe defaults and checks duplicate IDs, YAML
  keys, references, workflows and owner consistency. Missing/malformed config is
  an explicit error, never an empty fleet. ConfigFile updates use flock, revision
  CAS, fsync and atomic replacement. This is a cooperating-writer boundary.
- Config is declarative: observed permission/management/capacity claims must be
  unknown, not asserted current. Historical enrollment evidence must be imported
  separately and authenticated by the collector; binding_for returns an
  UNVERIFIED declaration, not authority. Do not copy old observation IDs as proof.
- `Store()` defaults to `$HERMES_HOME/runners/state.sqlite` (otherwise ~/.hermes).
  Explicit paths support tests. Connections are thread-affine. WAL, FULL sync,
  nested transactions, backup-before-migration and future-writer refusal are used.
  Schema v1 adds generic validated records, tombstones and reducer state.
- `put/get/list`, `retire_runner`, `prune` manage data. Defaults retain observations,
  transitions and resolved incidents 30 days; terminal job/repair audit 90 days;
  tombstones 30 days. Active records never expire. Ephemeral/JIT retirement compacts
  observations; audit is independent. Retention uses last write time: collectors
  must not rewrite unchanged observations just to refresh timestamps.
- `save_health/load_health` persist pure reducer State across collector restarts.
  Commit the reducer state and related observations in one outer transaction.

## Health integration

`advance(State, Sample, now=epoch_seconds, policy=Policy(), healthy=bool)` consumes
one new collection cycle. `complete=True` is allowed only after all scope pages
succeed and unique registration count matches provider total. For absent runners
pass registration=None. Errors/partial results break confirmation streaks without
rewriting last-known registration/activity. Pass healthy=False for independently
failed required checks so online status alone cannot close an incident.

`project(state, now=..., checks=Checks(...), host_at=..., ...)` never changes evidence
or timestamps. Checks must be independently verified, not loaded from config.
Policy supports inventory/host TTL and offline confirmation interval. Disk thresholds
are supplied to disk_state, including per-host overrides. Freshness expires strictly
after TTL (defaults 120s/180s); offline confirmation needs two samples spanning 30s.
Maintenance overlays health and excludes readiness; it never drains a runner.
Busy is activity, not failure. Explicit failed checks may make a busy registration
unavailable while preserving its activity. Failed builds and duration alone do not
imply runner failure. Scope errors remain scope errors, not runner outages.

`fleet` prevents an all-healthy verdict for partial/empty/error/stale evidence.
`capacity` distinguishes declared advisory limits from actual enforced admission;
unknown free capacity is None, never a sum of idle registrations. These are internal
projections, not yet API response serializers.

## Tests

From repository root:

    uv run --no-cache --with pytest --with pyyaml --with fastapi --with httpx --with 'jsonschema[format]' python -m pytest -q -p no:cacheprovider plugins/runners/tests plugins/runners/contracts

All tests are offline, use fictional fixtures and injected clocks, and do not
resolve credentials or dispatch work. Deployment must provide PyYAML and jsonschema;
plugin import itself does not load those optional validation dependencies.
