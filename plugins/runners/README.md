# Runners registry foundation

Phase B supplies validation, persistence and pure health rules only. Import and
`register(ctx)` are inert: no tools, pollers, endpoints or mutations are enabled.
Collector/API deployment belongs to the next phase.

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

    uv run --with pytest --with pyyaml --with 'jsonschema[format]' python -m pytest -q -p no:cacheprovider plugins/runners/tests plugins/runners/contracts

All tests are offline, use fictional fixtures and injected clocks, and do not
resolve credentials or dispatch work. Deployment must provide PyYAML and jsonschema;
plugin import itself does not load those optional validation dependencies.
