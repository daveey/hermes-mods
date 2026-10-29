# Runners inventory and diagnosis

Validation, persistence, pure health rules, bounded GitHub/SSH collection and a
cached authenticated dashboard API. Import and `register(ctx)` remain inert:
collection starts only through the explicit supervised entry script. Mutation,
dispatch and workspace execution remain disabled. Repair sessions have a separate
default-off gate: enable only after live owner-routed duplicate-click acceptance.

## Collector and API deployment

For repair reconciliation, prefer `scripts/install_service.py --install
--hermes-launcher /absolute/hermes --core-root /path/to/hermes-core` (plus the
owner/config arguments below). The installer discovers the interpreter through
`hermes --print-runtime-command`; the isolated worker activates the installed PM
dependency generation, including its process-lifetime lease. It does not run the
full CLI bootstrap, install dependencies, finish source updates or start gateways.
PM activation may recover its publication journal. Re-run installation after a
runtime interpreter replacement; the service pins the discovered executable.
Validate before rollout with that executable:
`/absolute/runtime-python -I scripts/runtime_collector.py --core-root
/path/to/hermes-core --check-core`. This checks activation/imports without runner
config, credentials or collection, and reports sanitized error class/module names.
A compatible core must support `StartIntent.metadata`.

For a separately managed interpreter, install PyYAML, `jsonschema[format]` and
core's status-layer dependencies (including `python-dotenv` and `ruamel.yaml`),
then use `--python /absolute/python --core-root /path/to/hermes-core` instead.
This path does not activate PM dependencies. Both paths load core after the plugin
namespace and fail closed on missing/conflicting core. Test-environment imports
alone do not establish production readiness.
Create a private mode-0600 runtime JSON with `credentials` mapping opaque refs to
`{scope, env_var, key}` broker references (never token values), and `probes` mapping
opaque SSH refs to `{target, spec}`. Credential aliases sharing a provider quota
must use the same `key`. Targets contain hostname, username, identity_file,
known_hosts_file and port; specs contain platform, opaque-ref-to-exact-service
`services`, build_volume and explicit `tools`. Optional `job_repositories` maps
scope IDs to bounded repository allowlists; omitted means no job enrichment.

Linux specs optionally contain `lifecycle_sources`, keyed by an existing opaque
service ref. Trusted provisioning may opt into `intentional_drain` (boolean),
`admission_unit` plus `lane`, `maintenance_marker` (absolute path), `headroom`
(boolean), or `supervisor_log` (absolute path) plus `runner` (exact identity).
These are fixed at provisioning, never accepted from API requests. Checks only
read systemd state, bounded journal output, marker metadata and a bounded log tail.
No new privileges, service writes, admission changes or automatic provisioning
are introduced. Denied/missing sources remain unknown; old probes report configured
checks as not observed until updated. Journal record times are preserved, not
renewed by polling. Disabled/inactive is intentional drain evidence only when
explicitly configured; host-wide disk pressure alone cannot establish a lane hold.

Optional runner `operational_evidence` keeps holds separate from provider
registration and persistent/ephemeral lifecycle. Each source carries observation,
expiry and record timestamps; current holds suppress verified readiness. Old or
unavailable configured checks cannot silently pass. Supervisor withdrawal logs
are historical evidence only, not proof a label is still withdrawn.

Optional `readiness_evidence` exposes existing required/passed/failed/warned
check keys with their host observation timestamps and freshness. Its separate
`hold_explained_failures` subset never masks independent disk/tool/service
failures: a stopped service is explained only by exact-service, current,
explicit intentional disabled/inactive drain evidence. Failed or missing services
remain independent. Unknown required checks remain unknown even on a held runner.
Provider fields cannot supply this projection; unbound runners omit it. See
`contracts/README.md` for client semantics. No extra probes are performed.

Journal checks are deliberately fail-closed, not complete state machines:
`headroom` recognizes waits but has no authoritative clear event; admission
requires a fresh explicit pressure-free record to clear. Opting into these checks
can therefore leave readiness unverified indefinitely after log expiry or while
idle, even when the provider says online. Unknown is never inferred clear from
silence or an unrelated journal line. A stale hold is only last-known, not active.
Do not enable these checks if verified-ready counts are required without an
authoritative clear source. Remote clock skew can also make evidence stale.
Journal checks use only existing read access; denied access remains Not observed.
This feature neither grants journal permissions nor asks the probe to elevate.

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

Dashboard discovery mounts `/api/plugins/runners/v1`: GET `/fleet` (machines and slots for the Desktop panel;
see `desktop-plugins/runners/README.md`), GET `/enrollment`, GET
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

## Diagnosis-only repair transactions

Repair operations require a verified principal shared by REST and the core
`session.start_request` transport. An inventory token without a principal is not
enough. Enrollment advertises `features.repair_sessions` per owner and principal;
the Desktop never takes the selected chat's gateway as action authority.

POST `/repairs` reserves the physical host (or unmapped registration), persists an
immutable quoted diagnosis brief, and registers its stable core request ID.
Desktop invokes `session.start_request` in namespace `runners` on the captured
owner route, then opens the returned stored/lineage session on that same route.
GET `/repairs/{id}` reconciles via core; GET `/repairs` supplies a principal-scoped,
active-first latest-100 list for badges without per-row polling. Inspect includes
that principal's related host repairs. Collector-created Incident records with
dimension `investigation` identify diagnostic contexts, not confirmed failures.

The collector uses the owner's canonical SessionDB status service every 30 seconds;
it never dispatches a turn. Paused/completed initial turns retain the reservation.
Archival, cancellation or failure releases it only after an authoritative receipt.
Missing runtime or unreachable owner is not completion. Unknown outcomes do not
replay prompts. Only an authoritative absent/queued mapping permits an explicit
retry of the same durable request ID, recovering a pre-create/pre-claim crash.

POST `/repairs/{id}/verify` durably queues bounded independent rechecks. Two distinct
fresh provider cycles, fresh host/service/tool checks and all required capability
observations are necessary for `restored_unverified_build`; no build is claimed.
Forced verification probing stops after five minutes; a new explicit verify key
can reopen that window. Stale/unknown/failed evidence cannot satisfy it.

GET `/maintenance` returns the overlay CAS revision. POST `/maintenance` uses it as
`expected_config_revision` alongside the unchanged expected owner identity.
This overlay has an expiry and reason, silences alerts and excludes ready capacity,
but never drains jobs or alters provider evidence. Staleness stays visible.

The tools `runners_list`, `runners_inspect`, `runners_refresh`, `runners_repair` and
`runners_verify` use these domain services. Repair/verify require the actual owning
gateway runtime session; unsupported CLI/isolated runtimes fail explicitly.
There are no authorized restarts, cleanup, job cancellation or signing operations;
the only mutation path is the broker-guarded canary `service_start` below.
The separate repair ledger is additive and bounded to 100 active reservations and
10,000 audit/idempotency requests. It fails closed when full rather than forgetting
an ambiguous request. Generic Store retention does not prune this ledger.

## Verifying a relayed approval (`runners_receipt`)

A repair chat cannot treat "David approved it" in a message as evidence. `runners_receipt{repair_id, operation_id}`
is a read-only lookup of the recorded decision in the user-steps store (read-only SQLite connection, same owner
profile). It returns `verified=true` only when the operation is `effect=approve` with `executionAuthorized=true`,
its frozen scope is complete and names this repair's subject (runner id, service ref or runner display name, whole
token) in the granting part of the scope (the text before its Limits/Exclusions/Consequences headings, plus the
approve outcome); a subject named only under Exclusions is refused. The request must not be withdrawn, cancelled or
superseded, still be at the recorded revision, and the receipt at most 14 days old. It then lists `authorized_operations`: `diagnose` always, `service_start` only if the granting text contains a
non-negated start/recover phrase (it is not tied to a particular sentence; the request's structured exclusions/limits
and the Limits/Exclusions/Consequences sections can only restrict). Declined/revised decisions, other subjects, unknown or malformed operation ids
and a relay without a receipt are refused. It cannot create or consume a decision. It is advisory: the repair brief
tells the chat to act only on `verified=true`, but `runners_operate` does not itself demand a receipt. The hard limits
are unchanged and enforced by the broker (policy-listed canary unit, stopped-only `service_start`, no sibling targets).

## Guarded management operations (broker)

`runners_operate` is the only management entry point a repair session gets. It forwards
`{operation, target_id, repair_id}` for the active repair's own bound service to the
management broker over `/run/hermes-runners-broker/manage.sock`. It never runs a shell.
The broker (`broker/server.py`, engine in `operations.py`, credential bridge in
`credentials.py`) runs as its own OS identity `hermes-runners-broker` from a root-owned
copy of the code (`templates/broker.service`). Policy lives at
`/etc/hermes-runners-broker/policy.json` (root-owned, not agent-writable) and credentials
in `/etc/hermes-runners-broker/credentials` (broker-owned 0700/0600). Peers are
authenticated by kernel `SO_PEERCRED` uid against the policy's client list.

Enforced on every request:

- strict schema: unknown fields (`command`, `argv`, `unit`, ...) and non-opaque IDs are refused.
  Unit names and argv come only from policy.
- operation allowlist: `diagnose` (read-only) and `service_start`. Mutation is allowed only on
  policy targets marked `canary` and only when `management_operations` is true in broker policy.
  The plugin flag of the same name gates the tool as well. The observe-only collector keeps
  running with that flag on (it never calls the broker); it still refuses `dispatch` and
  `workspace_jobs`.
- host and service identity: the request host must equal the broker host. The unit's `Id`,
  `FragmentPath` and description fingerprint must match the provisioned service.
- the repair subject only: sibling services are refused, and there are no batch, reboot or
  host verbs.
- `service_start` runs only on a stopped unit. It needs no `Runner.Worker` in the unit cgroup
  or runner root, and a fresh (at most 30 s) GitHub runner GET that is not busy. Any
  busy/unknown/stale activity refuses. A non-blocking host-scoped flock and a 120 s cooldown
  apply. The broker then reads the unit back and reports `control_plane_started` /
  `restored_unverified_build`, never build health.
- restart/drain/quiesce of a running listener stay disabled
  (`restart_disabled_no_safe_quiesce`). GitHub has no per-runner stop-assignment API, SIGTERM
  races assignment, and label removal does not stop default-label jobs.
- install, upgrade, unregister, cache deletion, job cancellation and signing changes are
  refused with `separate_explicit_approval_required`. The broker holds no approval authority,
  so a supplied approval is `approval_invalid`.
- diagnostic and evidence text is returned as `{"trust": "untrusted_data"}` and never widens
  authority.

`broker/authorization.py` generates the polkit rule that grants the broker user only `start`
on listed canary units. With no canary listed, it grants nothing. It also provides the
install boundary check and the forced-command probe key line. The workflow-facing
execution-claim API is a separate service (`broker/claims.py`, below); `server.ROUTES`
serves only `management`.

Limitation: on a host where the agent's Unix user has passwordless sudo, OS separation
protects credentials and policy from the unprivileged agent but cannot stop a root-capable
agent. Mutation safety is then only as strong as that session's tool boundary.

## Workflow dispatch and execution claims

`GET /v1/workflows`, `POST /v1/jobs`, `GET /v1/jobs/{id}` and the `runners_run` (toolset
`runners_dispatch`, so pinned repair chats cannot dispatch) / `runners_job` tools share
`jobs.JobDomain`. GitHub stays the scheduler; nothing is ever built on the gateway.

- Allowlist: `runners.yaml` `allowed_workflows` (opaque IDs only). The private
  `$HERMES_HOME/runners-dispatch.json` maps each ID to repository, workflow file, trusted ref,
  runner labels, offline policy and named inputs. Clients never send names, refs or URLs.
- Gates, in order: `features.dispatch` and the workflow's `dispatch_enabled`; typed inputs
  (duplicate/unknown/unsafe values refused); per-principal rate limit; idempotency (same key +
  payload returns the same job; another payload is `idempotency_conflict`); claim broker
  reachable (owner socket + runner-facing health); a recorded successful claim from that
  workflow within `reachability_max_age_seconds` (runners proved they can reach and
  authenticate to the broker); an eligible online runner (else `no_eligible_runner_online`,
  rejected or queued per workflow, never a local fallback); source SHA reachable from the
  trusted ref, which must be protected.
- The intent (fresh `request_id`) is persisted and registered with the claim broker before
  GitHub is called. `request_id` and `source_sha` are workflow inputs and the run-name carries
  `[request_id]`. A timeout/reset/5xx after sending is `unknown_outcome`: reconcile by request
  ID, never resubmit. Several runs for one request bind to the broker's claim holder.
- `GET /jobs/{id}` is cached and schedules one background reconcile: exact run id, attempt,
  job id and assigned runner, workflow-ref SHA (`head_sha`) kept distinct from the checked-out
  source SHA. Explicit retry (`JobDomain.retry`) is a new request; GitHub re-runs cannot claim.
- Artifacts (`<prefix><request_id>`) are downloaded only after completion: provider digest
  must match, the zip is extracted with traversal/symlink/size/count limits, and
  `provenance.json` must bind request, source SHA, run, attempt and every file's sha256.
  Expired artifacts are reported, never rebuilt.
- Canary mode (`submit(..., canary=True)`) exists only for in-process owner scripts and only
  for workflows marked `canary`; it is how the first runner-reachability proof is produced
  while `features.dispatch` is still false. REST and tools cannot reach it.

`broker/claims.py` runs as its own identity `hermes-runners-claims`
(`templates/claims.service`), stdlib only, no management or GitHub credentials. Owner side:
`/run/hermes-runners-claims/intent.sock` (`SO_PEERCRED` uid allowlist) for
register/status/reachability. Workflow side: `POST /v1/claims` on the tailnet address with a
GitHub Actions OIDC token. It verifies RS256 against the issuer JWKS (no other algorithms or
header keys), issuer, audience, expiry/nbf/iat with 60 s skew and 1 h maximum lifetime,
repository, `workflow_ref`/`job_workflow_ref` = policy workflow at the trusted ref, `ref`,
`event_name=workflow_dispatch`, `runner_environment=self-hosted`, and that the body's request
ID and source SHA equal the registered intent. The first run/attempt wins atomically; the
same run and attempt get the same grant again (lost response); re-run attempts and other runs
are refused forever (no lease expiry or takeover). Rate limits per source and globally.

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
