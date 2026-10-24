# Proposed Runners v1 contracts — Phase A

**Design artifacts only. No plugin, endpoint, tool, collector, executor or authorization
boundary is implemented here.** The exact proposed REST namespace is
`/api/plugins/runners/v1`. The source baseline reviewed for transport/session seams is
core `c20523a1754d21fa59f544ecc40d2ac880909a62`; that is not a deployment claim.
These contracts implement the design vocabulary from plan §§4, 6 and 14, with
normative future-runtime requirements below. Fictional examples are not an inventory.

## Files and validation

| File | Purpose |
|---|---|
| `runners.schema.json` | JSON Schema 2020-12; root validates normalized `Config`; `$defs` names validate domain, API/tool request and response documents. |
| `operations.json` | Complete §6 API and §6/§14 tool inventory, input/output schema names, policy and HTTP success codes. |
| `fixtures.json` | Invented standalone positive examples keyed by definition name; not a deployable inventory or evidence of readiness. |
| `build_contracts.py` | Deterministic offline authoring utility for those three JSON files; no runtime imports/network/credential lookup. |
| `test_contracts.py` | Offline unittest/jsonschema contract checks, including negative payloads. |
| `README.md` | Semantics, limitations and integration gates. |
| `FILE_MAP.md` | Proposed implementation boundaries and serialized ownership. |

From the repository root:

```sh
python3 -B plugins/runners/contracts/build_contracts.py
uv run --with 'jsonschema[format]' python -B -m unittest discover -s plugins/runners/contracts -p 'test_*.py' -v
```

Use `Draft202012Validator` **with `FormatChecker`**. Schema defaults are annotations,
not insertion: all fields are required, including nullable unknown values. A future
private YAML loader must explicitly normalize defaults before validation, refuse
unknown/duplicate keys, then atomically validate and replace a complete configuration.
Malformed configuration is `configuration_error`, never an empty successful inventory.
No migration, environment reader, live probe, runtime API test or publication is supplied.

## Identity, configuration and ownership

* Inventory ownership is `{gateway_id: UUID, profile, owner_epoch, config_revision}`.
  Owner epoch fences controller changes; configuration revision guards concurrent edits.
  Both must match authoritative owner state before mutation.
* A `ClientRoute` is `{connectionId, mode, profile, targetProfile}`: a separately resolved
  Desktop route, not a stable gateway ID or authorization credential. Connection IDs
  must be nonempty. `profile` selects the Desktop route, `targetProfile` the backend
  profile. Never serialize a current UI connection as registry ownership.
* Host `id` is a configured UUID; `resource_id` is its opaque operation handle. Mapping
  the latter to the former belongs to the owner registry. Runner identity is provider
  + endpoint + scope + provider runner ID, represented by an opaque registry handle.
  Names, labels, addresses and UI selection are never identity. Re-registration is a
  new registration with optional explicit `replacement_of`, not a name-based merge.
* Every operation target is an opaque ID matching `[a-z]{2,12}_[0-9a-f]{16,64}`.
  A matching string is **not an allowlist grant**. The future domain service must look
  it up by resource type, authenticated owner/profile, exact action and current policy;
  reject absent, cross-owner, wrong-type or revoked handles. Registry IDs are not secrets.
  No operation accepts hostname, shell, arbitrary URL, path, SSH option or raw credential.
  Provider IDs and core-owned session/turn IDs are preserved separately, not minted or
  rewritten by the plugin. Their bounded response representation grants no authority.
* Private `Config` includes scopes, hosts, explicit runner bindings, capacity, workflow
  allowlist, credential references, providers, verified BB bindings, execution targets,
  retention and thresholds. Hosts and provider scopes are not inferred from display names.
  Observed fields in this normalized model are collector-owned; declarative enrollment
  cannot assert capability verification merely by supplying an observation ID.
* Feature flags explicitly default to observe only. Repair sessions, management,
  dispatch and workspace jobs are false. False workflow `dispatch_enabled` is an
  additional gate. Even true configuration flags cannot waive runtime gates.
* The initial endpoint allowlist is deliberately just `https://api.github.com`.
  Enterprise scope is representable, not advertised supported. GHES requires a reviewed
  endpoint allowlist extension and compatibility tests; do not relax to arbitrary HTTPS.
  Credential/SSH/service/catalog/policy references resolve only through authorized
  private registries, never string interpolation into a command or network address.
* Schema rejects extra fields (including secret-shaped fields) and malformed references.
  It cannot identify a secret pasted into a legitimate bounded free-text field, establish
  ownership, verify an endpoint's network routing, compare clocks, check uniqueness by
  ID across arrays, validate foreign keys, or authenticate evidence. These remain
  semantic/runtime validation and redaction requirements, not passes claimed here.

## Domain and state semantics

`Host`, `Scope`, `Runner`, `Capability`, `Observation`, `Incident`, `Repair`, `Job`,
`Provider`, `ExecutionTarget` and `BBBinding` are independent definitions. `RunnerBinding`
and `BBBinding` require explicit verification observation references. A BB node and a
GitHub registration never become interchangeable because they share a host UUID.
Capability declarations and observed versions/expiry/verifier are separate. Provider
permission checks include allowed/denied/unknown/not_checked and evidence references.

Registration: online/offline/missing/unknown. Activity: idle/busy/unknown. Management:
reachable/unreachable/unknown. Readiness: pass/warn/fail/not_checked. Freshness:
fresh/stale/unknown. Maintenance and repair lifecycle overlay these independently.
Nullable host, assignment, job IDs, timestamps and capacity mean **unknown**, not zero.
Busy is not unhealthy; a down hub is a dependency failure, not proof of dead pods.

Normative projection requirements for later phases:

* Missing requires two complete successful paginated enumerations with total parity;
  authentication failure, partial coverage or timeout cannot remove registrations.
  Expected ephemeral/JIT expiry is retirement, not a missing incident. Tombstones last
  30 days; observations/transitions 30 days, repair/job audits 90; active records survive.
* Confirm offline only after two fresh offline observations at least 30 seconds apart;
  close incidents only after two healthy cycles. A first offline observation is suspected.
* Ready requires fresh online+idle and independently passing mandatory capabilities.
  Stale ready is not capacity. Inventory TTL is 120 seconds, host evidence 180 seconds.
  Never renew evidence expiry merely by serving cached responses or accepting a refresh.
* Keep registration counts distinct from physical hosts/available slots. Declared
  capacity is advisory unless actual admission is enforced; independent services may
  oversubscribe. Workflow concurrency is not a cross-repository lock.
* Scope errors are displayed once at source level. Incomplete coverage or stale evidence
  cannot yield an all-green fleet. Maintenance is not draining and does not hide staleness.

## Response envelope and errors — applies to every API and tool

Every success is `{meta: Envelope, data: <typed payload>}`; every domain error is
`{meta: Envelope, error: Error}`. `operations.json` identifies the success definitions;
`ErrorResponse` is the common failure definition. No bare colored verdicts.

`Envelope` carries contract version, request ID, stable owner, generated_at, nullable
observed_at/expires_at, explicit freshness, per-source provenance, per-scope coverage and
source errors. Provenance includes source/collection/observation IDs and source timestamps.
Coverage records enumeration count, provider total, pagination completion and explicit
complete/partial/denied/unavailable/not_configured/unknown state. Overall observation time
and expiry must conservatively reflect the relevant sources, never the freshest source
alone. With no observations, times are null and freshness unknown. `generated_at` is not
observation time. Mutating responses carry evidence age too; acceptance isn't verification.

Errors must not echo submitted secrets, raw provider responses or untrusted log text.
401 unauthenticated; 403 forbidden/feature_disabled; 404 not_found (no existence leak);
409 owner/revision/idempotency conflicts, busy or unknown activity; 422 invalid_request,
unsafe_target or stale_evidence; 429 rate_limited; 501 unsupported_runtime; 503 disconnected
owner/dependency unavailable; 500 internal_error. `unknown_outcome` after a possibly accepted
submission is a persisted job/repair state and reconcile response, not permission to retry.
Transport failures can preclude any envelope: clients synthesize an explicit disconnected
or plugin-unavailable view with last-known stale observations, not invented server data.

All entry points require existing gateway authentication, profile authorization and
same-origin/CSRF protection consistent with the authentication mode. Separate read/manage/
dispatch checks even for single-principal tokens; connection ID is never auth. Audit actual
principal when available plus request/connection/session IDs. Rate limit and bound outputs.
No claims of fine-grained RBAC before principal granularity is tested.

## REST contract

All paths below are relative to **`/api/plugins/runners/v1`**. Schema names are exact `$defs`.
For path `{id}`, remove its corresponding `runner_id`, `repair_id` or `job_id` from the JSON
body/query and supply it from the path; reject a duplicate or contradictory body ID. GET
has no body. `history_limit` is the only runner-detail query field (0–100; default 50).
The transport-level `profile` query is separate from domain input and must match owner
profile; never accept a caller-selected profile as authorization. Other GET inputs are
empty or the path ID. POST request fields otherwise go in JSON. Unknown query fields
must be rejected, not silently ignored. All reads are cached, not inline remote probes.

| Method/path | Input | Success data / status | Required semantics |
|---|---|---|---|
| GET `/inventory` | EmptyRequest | Inventory / 200 | Includes collection status, typed entities and separate counts; explicit empty/error states. |
| GET `/runners/{id}` | InspectRequest | RunnerDetail / 200 | Bounded observations, incidents, repairs, exact jobs. |
| POST `/refresh` | RefreshRequest | Collection / 202 | IDs only; empty targets means authorized inventory; deduplicated singleflight, no remote mutation. Poll inventory collection state. |
| POST `/repairs` | RepairRequest | RepairResult / 202 | Explicit diagnose/repair, incident, owner and idempotency; durable reservation before session start. |
| GET `/repairs/{id}` | RepairLookupRequest | RepairResult / 200 | Session lineage/status plus verification references; route may be unresolved. |
| POST `/repairs/{id}/verify` | VerifyRequest | Collection / 202 | Bounded independent rechecks; never accept `healthy=true`; poll repair and inventory. |
| POST `/maintenance` | MaintenanceRequest | Maintenance / 200 | Revision CAS, future expiry required when active, reason for both start/end; no job draining. |
| GET `/workflows` | EmptyRequest | Workflow[] / 200 | Only authorized workflow IDs; `input_schema_ref` resolves a reviewed finite typed-input schema via same-domain registry. Not arbitrary JSON Schema execution or URLs. |
| POST `/jobs` | JobRequest | Job / 202 | Allowlisted workflow, exact source SHA, typed named inputs, persisted idempotency before dispatch. |
| GET `/jobs/{id}` | JobLookupRequest | Job / 200 | Exact run/attempt/job, checkout SHA distinct from workflow-ref SHA, assignment/artifacts/unknown outcome. |

Workflow `input_definitions` exposes the reviewed finite input schema alongside its revision
handle `input_schema_ref`: input ID, display label, string/number/boolean type, required flag
and allowed values (empty means no extra enum constraint, not arbitrary structure).
Validate input ID membership, required inputs, duplicate IDs, type/allowed-value consistency
and injection risk server-side.
It does not make arbitrary strings safe shell interpolation. No workflow inputs are trusted
as OIDC claims. Artifact links are represented as authorized retrieval handles rather than
caller-supplied or arbitrary external URLs; adapters may render validated provider links.

## Tool contracts

All tools reuse the identical domain policy/envelope and definitions in `operations.json`.
Path IDs are ordinary tool input fields. No ambient selected-chat target fallback.

| Tool | Input → output definition | Gate |
|---|---|---|
| `runners_list` | EmptyRequest → InventoryResponse | read |
| `runners_inspect` | InspectRequest → InspectResponse | read |
| `runners_refresh` | RefreshRequest → RefreshResponse | read + bounded refresh authorization |
| `runners_repair` | RepairRequest → RepairResponse | manage + repair_sessions |
| `runners_verify` | VerifyRequest → VerifyResponse | manage; independent checks only |
| `runners_run` | JobRequest → JobResponse | dispatch + workflow allowlist + claim gate |
| `runners_job` | JobLookupRequest → JobResponse | read |
| `execution_resolve` | ExecutionResolveRequest → ExecutionResolveResponse | read; intended compact eager tool |
| `execution_run` | ExecutionRunRequest → ExecutionRunResponse | dispatch; revalidate resolution, owner and provider gates |
| `execution_status` | ExecutionStatusRequest → ExecutionStatusResponse | read on preserved owner/provider |
| `execution_cancel` | ExecutionCancelRequest → ExecutionCancelResponse | dispatch + separate production-cancel approval |
| `execution_artifacts` | ExecutionArtifactsRequest → ExecutionArtifactsResponse | read; checked retrieval handles |

`execution_artifacts` is the proposed concrete name for §14 artifact retrieval. Detailed
execution tools are deferred; no eager registration is installed by these files. A generic
management-shell tool is intentionally absent. Any future narrow operation tool needs a
separate allowlisted contract after broker/quiescence gates pass.

### Repair and exact owner routing

Idempotency uniqueness includes owner/profile, authenticated authority, operation and key;
store canonical request digest and reject different payload reuse. Reserve host-scoped
repair (runner-scoped if unmapped) before creating a visible session. Persist the repair's
foreign `start_request_id`; core owns stored/runtime/lineage/first-turn identity.

Required **new** RPC: `session.start_request({profile, namespace:'runners', start_request_id})`.
Resolve intent through an authenticated registered service boundary, not a localhost
callback or caller-supplied prompt. Durably commit mapping/session/unique first turn before
scheduling. ACK means durable acceptance, not completion. Missing runtime capability means
unsupported; never fall back to blind create+submit. Use `hidden:false` and
`close_on_disconnect:false`. Reconcile ambiguity; reopening never resends the first turn.

Current source has memory-only creation idempotency; ordinary drafts may be lazy, but
seeded sessions and explicit cwd can persist, minted keys have restart provenance, and
prompt submission has persistence paths. None establishes the new durable-start contract.
Hosted plugins and isolated compute need explicit integration, not an import of gateway
globals. Typed durable lineage/archival/turn status is still required.

Desktop resolves `ClientRoute` independently against its current registered routes.
Backend records cannot know every client's connection ID: `RepairResult.owner_route` may
be null (`unresolved`); a client transport adapter may attach a verified full route.
Do not persist it as authoritative. Exact proposed current-SDK REST adapter:

```js
ctx.rest('/v1/inventory?profile=' + encodeURIComponent(ownerRoute.targetProfile), {
  connectionId: ownerRoute.connectionId
});
```

Capture/validate nonempty route before awaits; verify response stable owner/profile;
cache by gateway UUID/profile. For start RPC use `host.requestProfile` on the captured
route and explicitly supply backend profile. For opening, use `host.openSession` with
full route, awaitHydration/forceResume/expectHistory/keepAllProfilesScope after durable
acceptance. Query scoping does not prove backend-dial topology compatibility. No ambient
`host.request`, focused gateway fallback, or untagged session-ID open. Conflicting owner
claims disable mutations. Disconnected owner remains visible and cannot be replaced.

### Execution, BB and workspace boundaries

Resolver input captures recipe, mode, optional exact target, capability IDs, source,
resource/deadline/output budget, permitted effects and artifact requirements. Null exact
target means policy selection, not local fallback. A resolution is a short-lived immutable
server record bound to owner/principal, request digest and policy revision. Submission
accepts its ID and a candidate target, not revised shell/source/credentials. Recheck all
freshness, authority and capacity at admission; resolution is not a reservation or grant.

Select explicit target first (fail rather than substitute); otherwise eligible recipe and
project policy, prefer dedicated always-on hosts. Match repo/group eligibility, not just
fleet capability. Swift alone does not require macOS. Registered recipe preflight may
reject incompatible local execution; unknown terminal commands retain their semantics,
with only narrow hints. Actual agent routing and terminal/execute_code paths need tests.

GitHub execution requires protected-ref source reachability verified by server and workflow,
request correlation, a durable atomic single-use claim before costly work, and authenticated
OIDC bound to the persisted exact request/repository/workflow/ref/source/run/attempt. Lost
response retries by the same winner only; no lease takeover or automatic redispatch.
Signing is separately authorized. No fallbacks to controller/personal laptop builds.

BB provider uses paired client/daemon → existing hub/Iroh RPC → authorized target command →
receipt, with explicit host binding and current published catalog. Node/hub/catalog handles
resolve privately, never a new direct pod authority. Preserve named BB capabilities and
platform approvals; no flattening to shell. Respect quiet/stop, presence, voice budget and
existing grants. Older pods retain existing capabilities; missing new protocol is unsupported.

Workspace mode is **new, disabled scope**, not `script.run` repurposed. Proposed internal
wire schemas `WorkspaceSubmit`, `WorkspaceStatus`, `WorkspaceLogsRequest`, `WorkspaceLogs`
are adapter contracts, not extra REST endpoints. Negotiate version before use:

| Operation | Wire contract / invariant |
|---|---|
| submit | WorkspaceSubmit → WorkspaceStatus; request/attempt, exact target/recipe, source manifest/digest, job-owned lease, argv/env policy refs, deadline/budget, scoped grant. Adapter derives argv/env from reviewed recipe; no raw shell. |
| status | Exact job/request/attempt → WorkspaceStatus; durable receipt and source digest, never latest-job lookup. |
| logs | WorkspaceLogsRequest → WorkspaceLogs with bounded redacted text, cursor and truncation; wrap all results in Envelope. |
| cancel | ExecutionCancelRequest → WorkspaceStatus; cancel_requested is not cancelled until process group/descendants cannot continue writing. |
| artifacts | ExecutionArtifactsRequest → ArtifactAccess[] with digest, size, SHA/source digest/run/attempt/toolchain provenance. |

Snapshots are content-addressed base SHA + exact patch + selected untracked files, with
manifest/digest validated end to end. Exclude secrets/unrelated files; reject traversal,
symlinks and oversized extraction; use job-owned workspaces, not interactive checkout.
Build workers run under separate supervised account, not pod voice/UI/TCC authority.
Reconnect/restart resumes observation of the same durable job. Conflicting work fails
closed unless shared admission actually exists; GitHub busy observation isn't a mutex.
Artifacts require completion/checksum/size verification; expiry is explicit, never rebuild.

## Open integration gates (not solved by schema tests)

Owner-route topology matrix (shared/dedicated/translated profile), same stored session ID
on two gateways, missing/replaced routes, real principal/profile/CSRF enforcement; durable
start crash/replay and lineage/turn reconciliation in hosted-plugin and isolated-compute
runtimes; singleton collection and pagination/retention/health semantics; private reference
resolution and redaction; broker separate UID/credentials, host lock and tested safe listener
quiescence (busy/unknown refuses restart); workflow claim/OIDC reachability and canaries;
BB enrollment/action/readback and workspace lifecycle/cancellation/voice isolation;
native UI screenshots/accessibility/performance; live inventory and permission parity.
Management/dispatch must stay disabled until their gates pass. This directory proves
only offline structural contracts and explicit rejected payloads, not any of those gates.
