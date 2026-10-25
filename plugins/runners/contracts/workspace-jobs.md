# Workspace-job wire contract v1 (reviewed revision 1.1)

> Contract revision **1.1**, reviewed. Wire protocol remains 1, with mandatory security.profile.v1.
> **Read §17 before implementing any section below.** §17 replaces the pre-review rules for worker
> identity/transport, sandbox/evidence, reap/UID reuse, full-tree/secret validation, hello/fencing/receipts,
> revocation/drain, capacity/exclusivity, negotiation/encoding and RPC load. Earlier examples and matrix rows
> are retained for context, not permission to implement a superseded rule. In particular: no tracked-secret
> exception; no same-UID exit evidence; no unsandboxed allow-network jobs; no executable advisory slots;
> no feature-off blocking of cleanup; no 20-second claim poll; no safety features optional by recipe.
> This is a reviewed design, not a claim of deployed implementation or live authorization.

Status: reviewed design for the workspace-job provider, pending implementation. The
plugin does not implement it yet. The workspace flag stays false until the runtime gates below pass. This is the
public-safe copy: it contains no private hosts, node IDs, accounts or inventory, and every identifier in it is
fictional. It sits under the Phase A adapter schemas in this directory (`WorkspaceSubmit`, `WorkspaceStatus`,
`WorkspaceLogsRequest`, `WorkspaceLogs`, `Source`, `Budget`, `Artifact`, `ArtifactAccess`, `ExecutionCancelRequest`),
which do not change. §3 shows how one maps onto the other.

Scope: plan §14 "Workspace jobs: capability, not remote-shell alias" and unit K. Workspace mode is the explicit
route for **unpublished edits** on an opted-in build host. Committed or approved source keeps using the GitHub
provider.

## 1. Decisions

1. **Transport.** Workspace jobs use dedicated, authenticated hub RPC operations `workspace.*` over the BB hub's
   existing peer-authenticated transport. They reach the same hub entry point that binds the authenticated node and
   checks revocation for every request. Workspace jobs are *not* a hub command forwarded through the shared MCP
   external scope. That scope cannot attribute calls to an individual client, so per-client grants are impossible
   there. They are also not `remote.submit` (which stays denied for agent clients), not a pod capability, and never
   `script.run`. The local client daemon forwards only an explicit allowlist of ops; the submitter ops are added to
   that list, and worker ops never are.
2. **The worker is a separately enrolled node, not a pod.** It is a dedicated `bb-workspace-worker` service with its
   own transport key and its own enrollment. Its role is the least-privileged enrolled client class, and it is
   additionally registered as a worker. Pod code, capabilities and device authority are unchanged. Speakers,
   displays and phones cannot advertise workspace jobs.
3. **Worker least privilege.** A registered worker node may call only `workspace.worker.*`. Every other op is
   refused with `forbidden: ws:worker_only`, on every transport, immediately after the revocation check. A
   non-worker calling a worker op gets `forbidden: ws:not_worker`.
4. **Separate hub state.** The hub keeps a workspace database and a content-addressed blob store apart from the
   main hub database. Write transactions never span network or blob I/O.
5. **Execution identity.**
   - A supervisor account holds the worker key, journal and source mirror, all mode 0700. It has no GUI login and
     runs as a system-domain background service.
   - Jobs run only as a per-slot unprivileged account (fictional names: `_wsworker` for the supervisor, `_wsslot1`
     for a slot). The supervisor launches them through a fixed-argv sudoers rule for two root-owned helpers,
     `shepherd` and `reap`.
   - Jobs therefore have no GUI session, TCC grant, keychain, microphone, camera or screen access, and cannot read
     the worker credential.
6. **Verified cancellation.**
   - The worker signals the process group, then kills every process owned by the slot uid (`kill -KILL -1` run as
     the slot user), then enumerates processes by uid.
   - `descendants_stopped=true` requires two enumerations at least 500 ms apart that both find zero processes.
     After that nothing can write as that uid, and the job workspace is 0700 to that uid.
   - Feature `cancel.uid_reap.v1` provides this on macOS. A Linux worker must prove the same thing with a cgroup v2
     scope (`cancel.cgroup.v1`: `cgroup.kill`, then an empty `cgroup.procs`).
7. **Admission.**
   - It is an atomic hub ledger over dedicated slots of one exact target worker.
   - GitHub busy status, Runners inventory and UI state are never admission inputs.
   - `dedicated_slot` mode cannot prevent another executor from starting later, so its overlap is reported as
     advisory. Only `dedicated_host` is exclusive.
8. **Only reviewed recipes run.** The submitter sends `recipe_id`, `recipe_digest` and typed parameters. The worker
   builds argv and env from its own root-owned recipe file. There is no raw argv, no shell string and no caller env.
9. **Wire formats.** Timestamps are integer Unix seconds. IDs are lowercase canonical UUIDs. Digests are
   `sha256:<64 lowercase hex>`. Canonical JSON never contains floats.

## 2. Authority model

| Principal | Recognised by | May call |
|---|---|---|
| Submitter | An authenticated agent-client node that is not a registered worker, is non-revoked, and holds at least one active grant | `workspace.hello`, `workers.list`, `source.*`, `submit`, `get`, `logs`, `cancel`, `artifacts.*`, limited to its own grants, sources and jobs |
| Worker | An authenticated enrolled node registered and enabled in the workspace worker registry, non-revoked | `workspace.worker.*` only |
| Owner admin | A local shell on the hub host only, through a local admin tool that has no RPC exposure | Register/disable workers, add/revoke grants, hold/unhold, `release-slot` after manual host verification, export audit |

Grants:
- Grants are additive and audited. Their fields are `{grant_id, submitter_node, worker_id, recipe_ids[],
  max_active_jobs (default 1), max_deadline_seconds, network_allowed, created_at, created_by, expires_at (≤ 90
  days, required), revoked_at, revoke_reason}`.
- A grant broadens no other command and changes no existing policy matrix or served catalog.

Audit:
- An append-only audit records admin actions, denials, admissions, claims, cancels and terminal transitions, with
  digests of canonical args. Raw source, logs and secrets are never recorded.
- Retention is 90 days. Rows for active jobs never expire.

Stop, quiet and holds:
- The hub does not observe pod stop state, so admission does not claim to be stop-aware.
- An owner hold, either on the hub or as a worker-local root-owned hold file, refuses new admissions and claims
  (`worker_hold`). Running jobs continue until explicitly cancelled.
- Jobs produce no sound and cannot reach audio devices.

Live privileges that need explicit approval, per item, when they are reached:
- build-host OS accounts, sudoers rule and service install;
- worker enrollment and registration;
- the first grant, and enabling the hub feature flag.

## 3. Identifiers and Phase A mapping

| Wire | Minted by | Hermes opaque form |
|---|---|---|
| `request_id` | Hermes (UUIDv4, persisted before first send) | `wsreq_<uuid hex>` |
| `attempt_id` | Hermes (a new one only for an explicit retry) | `wsatt_<uuid hex>` |
| `job_id` | hub at admission | `wsjob_<uuid hex>` |
| `lease_id` | hub at admission | `wslease_<uuid hex>` |
| `worker_id` | admin registration (never the node ID) | `wsworker_<uuid hex>` |
| `artifact_id` | hub at artifact commit | `wsart_<uuid hex>` |
| `receipt_digest` | hub | `wsrcpt_<64 hex>` |
| `recipe_digest` | worker | Phase A `argv_policy_ref` = `env_policy_ref` = `wsrcp_<64 hex>` |
| log cursor (byte offset) | hub | `wscur_<offset, 16 hex>` |
| `upload_id`, `session_id`, `cancel_id`, `claim_id` | hub / hub / Hermes / worker | not exposed |

Transport request IDs are never application IDs. Lookups are always by `job_id` or `(request_id, attempt_id)`;
there is never a "latest" lookup.

## 4. Negotiation

`workspace.hello` and `workspace.worker.hello` both take
`{protocol_versions:[1], features:[…], software:"name/version"}`.

The reply is:
`{protocol_version:1, features:[intersection], hub_features:[…], enabled:bool, limits:{max_chunk_bytes:524288,
max_source_bytes:268435456, max_files:2000, max_file_bytes:33554432, max_patch_bytes:33554432,
max_log_bytes:67108864, max_artifact_bytes:1073741824, max_artifacts:32, max_deadline_seconds:14400,
claim_wait_max_seconds:20, logs_read_max_bytes:65536}, server_time}`.

v1 features: `source.snapshot.v1`, `source.commit.v1`, `logs.cursor.v1`, `cancel.uid_reap.v1` or
`cancel.cgroup.v1`, `artifacts.sha256.v1`, `resume.v1`, `receipts.v1`, `network.deny.v1`.

Downgrade and older peers:
- No common version gives `ws:unsupported_version`. A recipe needing a feature the worker lacks gives
  `ws:worker_feature_missing`.
- An older hub answers `invalid_request` "Unknown operation". An older local client daemon answers `invalid_op`
  "unsupported op". The provider maps both to `unsupported_runtime`.
- Existing device operations and GitHub routing are unaffected. Older pods are untouched.
- The hub feature flag defaults off. Every op except `hello` then returns `forbidden: ws:feature_disabled`.

## 5. Common rules

- **Size limits.** Requests must stay under the hub payload limit (2 MiB) and the client socket line limit (1 MiB).
  Binary data is base64, at most 512 KiB raw per call. Unknown argument keys are rejected.
- **Error format.** Errors use the existing RPC codes. Intermediate layers keep only code and message, so the
  message always starts with **`ws:<reason_code>: `**, and clients parse `^ws:([a-z_]+): `. Messages never echo
  submitted content, secrets or log text.
  - `forbidden`: feature_disabled, grant_denied, grant_revoked, grant_expired, not_owner, worker_only, not_worker
  - `conflict`: capacity_conflict, worker_hold, idempotency_conflict, attempt_conflict, stale_session,
    fence_conflict, state_conflict
  - `invalid_request`: unsupported_version, invalid_args, path_rejected, secret_path_rejected, patch_rejected,
    too_large, digest_mismatch, source_unsealed, recipe_mismatch, param_rejected, deadline_invalid,
    worker_feature_missing, not_found
  - `unavailable`: worker_unavailable, storage_full, rate_limited, hub_busy
- **Pre-submission refusals create nothing**: no job, ledger row or slot. Retrying the same IDs after fixing the
  cause is safe.
- **Idempotency.** Every mutating op is idempotent by its own key. An identical replay returns the stored result; a
  changed replay returns `idempotency_conflict`.
- **No long blocking.** Only `worker.claim` long-polls, for at most 20 s.
- **Rate limits.** Per node: submitters 20 ops/s burst and 5 ops/s sustained. Chunk ops are bounded by
  `max_source_bytes` per minute.

## 6. Source snapshots

### 6.1 Kinds
- `{kind:"commit", repository_id, sha}`
- `{kind:"snapshot", repository_id, base_sha, manifest_digest, upload_id}`: base commit, plus an exact patch, plus
  selected untracked files. It must be sealed before submit.

`repository_id` (`^[a-z0-9][a-z0-9._-]{0,63}$`) is a worker-local alias. The worker's config maps it to a fetch
remote and a read-only credential. The wire never carries a URL.

### 6.2 Canonical manifest v1

    {"base_sha":"<40 hex>",
     "files":[{"digest":"sha256:…","mode":"100644"|"100755","path":"<rel>","size":<int>}],
     "manifest_version":1,
     "patch":{"digest":"sha256:…","size":<int>}|null,
     "repository_id":"<alias>"}

Canonical bytes:
- keys sorted at every level;
- separators `,` and `:` with no whitespace;
- `files` sorted bytewise by `path`;
- plain base-10 integers;
- strings restricted to the ASCII charsets below, so no escapes ever occur.

Digests:
- **Source digest** = `sha256:` + hex(sha256(canonical bytes)).
- Commit source digest = `sha256:` + hex(sha256(`"commit:" + repository_id + ":" + sha`)).

Vector (fictional). The patch blob is the 35 bytes `diff --git a/README.md b/README.md\n`. The file
`Sources/Demo/notes.txt` is `hello\n`. Canonical manifest:

    {"base_sha":"0123456789abcdef0123456789abcdef01234567","files":[{"digest":"sha256:5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03","mode":"100644","path":"Sources/Demo/notes.txt","size":6}],"manifest_version":1,"patch":{"digest":"sha256:cd51f00342bad0092b0f3e9757611572579bb33eacd900acda4e97c8da38241d","size":35},"repository_id":"demo-app"}

    source digest = sha256:94730f8005e6a8533f7875eb62ddd47c663955c62c55bef1b102790b68c61c2f

### 6.3 Path, symlink and secret rules
The submitter, the hub and the worker each enforce these independently.

- Charset `^[A-Za-z0-9._@+-]+(/[A-Za-z0-9._@+-]+)*$`, total ≤ 512 bytes, segment ≤ 255. Non-ASCII is refused,
  which avoids Unicode normalization ambiguity.
- No `.` or `..` segment, no `.git` segment (case-insensitive), no absolute path, no empty segment.
- No case-insensitive duplicates. The worker also refuses case-fold collisions with the base tree.
- Modes are only `100644`/`100755`. Untracked inputs are `lstat`ed and must be regular files. The patch may not
  create, keep or change to/from mode `120000` (symlink) or `160000` (submodule).
- `files` may not overlap base-tree paths or patch paths.
- **Denylist.** It is case-insensitive on any segment or basename. A match refuses the whole snapshot with reasons
  reported; nothing is dropped silently.
  - Environment files: `.env`, `.env.*` (except `.example`/`.sample`/`.template`).
  - Keys and certificates: `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `*.keystore`, `*.ppk`, `id_rsa*`,
    `id_ecdsa*`, `id_ed25519*`, `*.mobileprovision`, `*.provisionprofile`, `*.keychain`, `*.keychain-db`, and new
    `*.cer`.
  - Credential files: `.netrc`, `_netrc`, `.npmrc`, `.pypirc`, `.git-credentials`, `credentials`,
    `credentials.json`, `service-account*.json`, `secrets.*`.
  - Credential directories: `.ssh/`, `.aws/`, `.gnupg/`, `.docker/config.json`, `.kube/config`.
  - Agent state: `.hermes/`.
  - Databases: `*.sqlite`, `*.sqlite3`, `*.db`.
  - Build and IDE output: `xcuserdata/`, `*.xcuserstate`, `DerivedData/`, `.build/`, `node_modules/`.
  - `.DS_Store`.
  - Edits to files already tracked in the base are allowed. A patch that *adds* a denylisted path is not.
- **Content scan (submitter).** Added patch lines and untracked contents are scanned with gitleaks, using the
  repository's config when present, or else built-in rules: private-key headers, cloud access keys, Git hosting
  tokens, chat tokens, API-key prefixes and JWT-shaped bearer values. A hit refuses the snapshot and reports rule
  and path, never the matched text.
- **Bounds:** ≤ 2000 files, ≤ 32 MiB per file, ≤ 32 MiB patch, ≤ 256 MiB total.

### 6.4 Patch
- Produced with
  `git -c core.quotepath=off diff --binary --full-index --no-color --no-ext-diff --no-renames --no-textconv <base_sha>`.
  It covers tracked files only, staged and unstaged. An empty diff gives `patch:null`.
- The worker parses headers first. It refuses rename/copy, symlink/submodule modes and paths failing §6.3, then runs
  `git apply --cached --check` followed by `git apply --cached`.
- An apply failure is `failed(patch_apply_failed)`; the build never starts.

### 6.5 Upload
- `workspace.source.begin {request_id, manifest}`: returns `{upload_id, source_digest, missing:[digests]}`.
  Idempotent by `(submitter, request_id, source_digest)`.
- `workspace.source.put {upload_id, digest, offset, data_b64}`: offsets must be contiguous. An identical resend is a
  no-op; different bytes are a conflict.
- `workspace.source.commit {upload_id}`: verifies all sizes and digests and seals the source.
- Blobs are deduplicated per submitter and never readable by another submitter. They are kept 7 days after the
  last referencing job (24 h if never submitted). The store is capped; when full, `source.begin` returns
  `storage_full` and never evicts live sources.

### 6.6 Materialized tree
- The worker builds the job index with plumbing only: `read-tree <base>`; `apply --cached`; per file
  `hash-object --no-filters -w` plus `update-index --add --cacheinfo`; then `write-tree` gives
  **`materialized_tree`**.
- It re-derives the manifest from received blobs, checks it against `source_digest`, and only then checks out the
  job workspace.
- The submitter computes the same tree in a temporary index and must match the receipt. A mismatch is
  `source_verification_failed` whatever the exit code.

### 6.7 Recipes
- Recipes live in a root-owned file (0644) that neither the supervisor nor the slot users can write. Otherwise the
  worker refuses to start.
- Recipe fields:
  `{recipe_id, repository_id, revision, argv:[literal | {"param":name}], params:{name:{enum:[…]} |
  {pattern:"…", optional:bool}}, env:{literal}, path:"<fixed PATH>", cwd:"src[/rel]", network:"deny"|"allow",
  features:[…], artifacts:[{name, path, kind:"file"|"tar", required, max_bytes}], max_deadline_seconds,
  default_budget}`.
- `recipe_digest` = sha256 of the canonical recipe JSON.
- Parameters:
  - each `{"param"}` becomes exactly one argv element, and an absent optional parameter removes its element;
  - there is no shell;
  - the hub and the worker both re-check the digest and parameters.
- Job env is only `PATH`, `HOME=<job>/home`, `TMPDIR=<job>/tmp`, the recipe literals, `BB_WORKSPACE_JOB_ID`,
  `BB_WORKSPACE_SOURCE_DIGEST` and `BB_WORKSPACE_CPU_SLOTS`.
- The worker refuses recipes whose literal env names look like secrets (`token|secret|key|passw|cred|auth`) or are
  `SSH_AUTH_SOCK`, `DYLD_*` or `LD_*`.
- `network:"deny"` requires `network.deny.v1`; without it the recipe is not offered. A grant with
  `network_allowed=false` refuses `allow` recipes.

## 7. Submitter operations

| Op | Args → result | Notes |
|---|---|---|
| `workspace.hello` | §4 | read-only |
| `workspace.workers.list` | `{}` → `[{worker_id, display_name, platform, slots, free_slots, state: ready\|busy\|hold\|stale\|disabled, last_seen_at, protocol_version, features, recipes:[{recipe_id, revision, recipe_digest, repository_id}], admission_mode: dedicated_host\|dedicated_slot, host_checks}]` | Only workers in the caller's grants. `stale` = heartbeat ≥ 15 s old. |
| `workspace.source.*` | §6.5 | |
| `workspace.submit` | `{request_id, attempt_id, retry_of_attempt_id\|null, worker_id, recipe_id, recipe_digest, params, source, deadline_seconds, budget}` → JobStatus | Ledger key `(submitter, request_id, attempt_id)` plus `args_digest`. A new attempt is allowed only when all prior attempts are terminal and `retry_of_attempt_id` names the latest one. |
| `workspace.get` | `{job_id}` or `{request_id, attempt_id}` → JobStatus | A job the caller cannot see returns `not_found`, with no existence leak. |
| `workspace.logs` | `{job_id, cursor\|null, limit_bytes ≤ 65536}` → `{job_id, source_digest, offset, next_cursor\|null, eof, truncated, text}` | `text` is redacted, UTF-8 with replacement characters. |
| `workspace.cancel` | `{job_id, cancel_id, reason}` → JobStatus | Idempotent. Production-cancel approval stays in the Hermes policy layer. |
| `workspace.artifacts.list` | `{job_id}` → `[{artifact_id, name, kind, size, digest, state}]` | Terminal jobs only. |
| `workspace.artifacts.read` | `{job_id, artifact_id, offset, length ≤ 512 KiB}` → `{data_b64, offset, chunk_digest}` | The client verifies every chunk and the final digest. |

JobStatus:

    {job_id, request_id, attempt_id, lease_id, worker_id, recipe_id, recipe_digest, source_digest,
     materialized_tree|null,
     state: accepted|queued|running|cancel_requested|cancelled|succeeded|failed|unknown_outcome,   // = Phase A enum
     phase: null|claimed|materializing|executing|collecting|reaping, reason|null, started, exit_code|null,
     signal|null, descendants_stopped: true|false|null, admitted_at, claimed_at, started_at, finished_at,
     deadline_at, log:{bytes, truncated, digest}, artifacts:[{artifact_id, name, size, digest}], receipt_digest|null}

## 8. State machine, receipts and resume

    accepted → queued (slot reserved in the same transaction) → running (claim) → succeeded | failed | cancelled
    queued → cancelled (started:false)       queued → failed(not_claimed) after admitted+120 s
    running → cancel_requested → cancelled (only with descendants_stopped:true)
    running|cancel_requested → unknown_outcome(worker_lost) after 900 s without the worker; the slot stays occupied
    unknown_outcome → terminal on a late finish by the same worker (recorded as reconciled_late)

Invariants:
1. **At most one launch per attempt.** Only a `queued` job can be claimed, and the claim moves it to `running`
   atomically. Nothing ever re-queues, takes over a lease or re-dispatches.
2. **Resume, never relaunch.** On any transport uncertainty the client calls `get(request_id, attempt_id)`.
   `not_found` means it was never admitted, so the client resends the identical submit. Otherwise it adopts the job.
   It never mints new IDs to recover.
3. **Slots are released only by:** a verified terminal finish, `not_claimed`, a queued cancel, or an audited admin
   `release-slot`. An `unknown_outcome` keeps its slot, so admission fails closed.
4. **Success conditions.** `succeeded` requires exit 0, every required artifact committed and verified, and
   `descendants_stopped:true`. Otherwise the job is `failed` with a reason. `reap_failed` also quarantines the slot.
5. **Cancelled jobs have no artifacts.** Collection happens only after reap and only when the outcome is not
   cancelled.

Receipt: canonical JSON (§6.2 rules), written before the finish is acknowledged:

    {receipt_version:1, job_id, request_id, attempt_id, submitter_node, grant_id, worker_id, worker_node,
     worker_software, lease_id, recipe_id, recipe_digest, params_digest, source_digest, base_sha|null,
     materialized_tree, state, reason, started, exit_code, signal, descendants_stopped, admitted_at, claimed_at,
     started_at, finished_at, deadline_at, log_bytes, log_truncated, log_digest,
     artifacts:[{artifact_id, name, kind, size, digest}] (sorted by name), toolchain:{bounded strings},
     reconciled_late}

`receipt_digest` = sha256(canonical receipt). Before reporting success, the client checks that the receipt's
source digest equals its own computed digest, that `materialized_tree` equals its local tree, and that every
downloaded artifact digest equals the receipt's.

## 9. Worker operations

| Op | Args → result | Notes |
|---|---|---|
| `workspace.worker.hello` | §4 + `{slots, platform, admission_mode, recipes, host_checks, active_jobs:[{job_id, fence, state, log_offset}]}` → §4 + `{session_id, cancels, adopt, unknown}` | Starts a new session. Ops from older sessions get `stale_session`. Running jobs missing from `active_jobs` become `unknown_outcome(worker_journal_missing)`. |
| `workspace.worker.heartbeat` | `{session_id, host_checks, slots_state, active}` → `{cancels, holds, server_time}` | Every 5 s; stale at 15 s. Cancels are delivered here. |
| `workspace.worker.claim` | `{session_id, claim_id, slot, wait ≤ 20}` → null or the assignment `{job_id, fence, lease_id, recipe…, params, source, deadline_at, budget}` | Idempotent by `claim_id`. In one transaction the hub re-checks grant, worker and submitter revocation, hold and deadline. A failed re-check turns the job `failed(started:false)`. |
| `workspace.worker.source.read` | `{session_id, job_id, digest, offset, length}` → `{data_b64, chunk_digest}` | Only blobs of the worker's own assigned jobs. |
| `workspace.worker.event` | `{session_id, job_id, seq, phase, started?}` → `{fence}` | Idempotent by seq. `started:true` only after the process exists. |
| `workspace.worker.log` | `{session_id, job_id, offset, data_b64}` → `{stored_bytes, truncated}` | Idempotent by offset. |
| `workspace.worker.artifact.begin/put/commit` | declared name/kind/size/digest; chunks → `{artifact_id}` | The hub verifies on commit. Only during `collecting`. |
| `workspace.worker.finish` | `{session_id, job_id, fence, outcome, reason, exit_code, signal, descendants_stopped, materialized_tree, log_digest, toolchain}` → `{receipt_digest}` | Idempotent. A different payload gives `fence_conflict`. `descendants_stopped` must be a boolean. |

Worker execution requirements:
- **Restart without relaunch.** The journal is fsynced before every side effect. A shepherd per job survives a
  supervisor restart, logs to a file, and writes its exit status atomically. Live shepherds are matched by pid plus
  process start time. If the shepherd is gone and there is no exit status, the worker reaps and reports
  `failed(worker_lost_process)`. The worker never respawns a job.
- **Workspace.** Each job gets its own workspace under the worker root, owned by the slot uid. Source comes from a
  read-only mirror, never an interactive checkout. The base must be fetchable, and the receipt records whether it is
  reachable from the configured protected ref.
- **Bounds.**
  - Wall deadline: monotonic.
  - Memory: RSS summed over slot-uid processes and **sampled** every 1 s.
  - Disk: sampled every 5 s.
  - Logs: truncated, not failed.
  - rlimits: NPROC, NOFILE and CORE=0 are set.
  - Priority: nice 10 and utility QoS. Hosts are configured so that slots × cpu_slots ≤ cores − 2.
- **Redaction.** Logs are redacted with the §6.3 secret rules by the worker, and again by the hub.
- **Artifacts.**
  - Only recipe-declared paths. Every entry is opened with `O_NOFOLLOW` and `lstat`ed; symlinks are rejected.
  - `tar` artifacts are deterministic ustar: sorted, mtime 0, uid/gid 0, normalized modes, no links or devices.
  - Clients extract with traversal, link and size-bomb protection.
- **Host checks.** Free disk below 20 GiB puts the worker on hold. A configured conflicting executor that is active
  causes a refused start (`failed(capacity_conflict_at_start, started:false)`).
- **Revocation.** If any worker op returns `forbidden`, the worker stops claiming and cancels and reaps all local
  jobs. The hub marks those jobs `unknown_outcome(worker_revoked)`.

## 10. Admission

Admission runs in one immediate transaction. Each check, in order, gives its error on failure:
1. The submitter is an authorized client and the feature is enabled.
2. Ledger replay or conflict.
3. The grant is active, unexpired, binds this submitter, worker and recipe, and allows the deadline and network
   policy.
4. The worker is enabled, non-revoked, unheld, has a fresh heartbeat, and has the required features and the
   advertised recipe digest.
5. The source is sealed, owned by the submitter and digest-matched.
6. Capacity: non-terminal jobs on the worker (including `unknown_outcome`) < slots, and grant active jobs < max.
   Otherwise `capacity_conflict`. There is no queueing behind a busy slot in v1.
7. Insert the job, reserve the slot, write audit, commit, reply.

Revocation that happens later (across awaits) is caught by the re-check at claim and on every heartbeat:
- a revoked grant or submitter while a job runs ⇒ `cancel_requested(grant_revoked|submitter_revoked)`;
- a revoked worker ⇒ the worker stops itself (§9).

## 11. Hermes provider (this repo)

| File | Status | Responsibility |
|---|---|---|
| `plugins/runners/providers/workspace.py` | new (K3) | Protocol client over the existing local client-daemon socket; ID mapping (§3); `ws:` and downgrade error mapping; durable intent before send; resume by `(request_id, attempt_id)`; logs, cancel, chunked artifact download; digest, tree and receipt verification. It reuses J's daemon-socket call if `providers/babybear.py` exposes one, and does not edit that file. |
| `plugins/runners/providers/workspace_source.py` | new (K3) | Snapshot builder (§6.2–6.4), secret scan, `lstat`/symlink refusal, local `materialized_tree`. |
| `plugins/runners/tests/test_workspace_provider.py`, `tests/test_workspace_source.py`, `tests/fixtures/workspace/` | new (K3) | §12 client rows. Fixtures are copied from the hub's shared vectors at a recorded source SHA. |
| `plugins/runners/execution.py` | edit after I | Workspace candidates are only `mode:"workspace"` targets with a BB binding. Eligibility requires the `workspace_jobs` flag, negotiated v1, and the worker listed with matching recipe digest and `ready` state. Unpublished or dirty source routes only to workspace mode. A missing target or an admission conflict fails closed with the reason. No fallback to the gateway, a laptop or another worker without a new resolution. |
| `plugins/runners/__init__.py` | edit, last in the hot-file order | No new eager tools. The existing `execution_*` tools dispatch to the provider. |
| `plugins/runners/store.py` | additive migration only if needed | Request, attempt and receipt fields for Job records. |
| `plugins/runners/README.md` | edit | A "Workspace jobs" section. |
| `plugins/runners/contracts/workspace-jobs.md` | this file | Corrected only for implementation-found errors, recorded in evidence. |

Hub-side and worker-side implementations live in the BB repository and are released through its own reviewed
pipeline. Their exact file ownership is recorded privately. The hub implementation also carries a BB-side copy of
this contract.

## 12. Test matrix (each implementation's acceptance includes its column)

Fault injection uses fakes or a dedicated idle canary worker only. No test may reach real credentials or real
dispatch.

| # | Scenario | Hub | Worker | Hermes client |
|---|---|---|---|---|
| 1 | Lost responses | Submit, source.put, claim, log and finish replays return the stored result; changed replays conflict | Finish retried with the same payload; claim retried with the same `claim_id` | Submit timeout → `get` adopts the job; `not_found` → identical resend; never new IDs |
| 2 | Reconnect | `get` by `(request_id, attempt_id)` | Helper restart resumes log shipping from the acknowledged offset | Transport drop mid-job resumes the same `job_id` and cursor (live: the journal shows one launch) |
| 3 | Worker restart | Hello adopts listed jobs and marks missing ones unknown; stale-session ops rejected | Re-attach to a live shepherd without respawn; gone shepherd → reap + `worker_lost_process`; PID reuse not adopted | `unknown_outcome` is never reported as success |
| 4 | Hub restart | Restart between commit and reply: no duplicate; ledger, slots, receipts and claim deadlines persist | Retries without state loss | Hub down → dependency unavailable, not failed |
| 5 | Revocation | No grant → forbidden; revoked before claim → failed not started; revoked while running → cancel; revoked submitter device; revoked worker; worker limited to worker ops on every transport; a client cannot call worker ops; worker stays least-privileged class; agent `remote.submit` denial unchanged | `forbidden` stops claiming and reaps all | Grant denied → authorization state, not outage |
| 6 | Process-tree cancel | Queued cancel is immediate; `cancelled` requires `descendants_stopped:true`; finish without the flag refused | Child, grandchild and `setsid` escapee all killed; **no writes after cancelled** (a grandchild appending to a file shows unchanged size and mtime over 3 s); reap failure quarantines the slot; timeout reaps | `cancel_requested` shown until verified (live: real tree) |
| 7 | Capacity conflict | Busy slot → conflict with nothing created; `unknown_outcome` holds the slot; concurrent submits → exactly one admitted; hold; stale worker; grant max | Conflicting executor at start → failed, not started; low disk → hold | Admission conflict fails closed with no fallback; `dedicated_slot` overlap marked advisory |
| 8 | Dirty-source digest | §6.2 vector; path, denylist and patch-header vectors; blob digest mismatch; unsealed source refused | Manifest vectors; malicious headers, symlink, submodule and case-fold refused; materialized tree matches fixture; digest mismatch refused before start | Vectors; untracked symlink and traversal refused; secret path and content refused without echo; local tree equals receipt tree; digest in every result |
| 9 | Artifact verification | Canonical receipt digest vectors; artifact commit digest mismatch refused; cancelled job has no artifacts | Deterministic tar; symlink artifact refused; undeclared paths ignored | Chunk and final digest mismatches refused; safe extraction |
| 10 | UI/voice non-interference | Existing ops unchanged; separate database; no write transaction across blob I/O | Job env has no inherited vars; job runs as the slot uid; self-test proves no GUI session, no keychain, and screen capture refused without a prompt | Device tools unaffected when workspace is unsupported. Live: host pod quiet, stop and listening status unchanged before and after (read-only) |
| 11 | Downgrade | Version intersection; unsupported version; feature off by default; missing worker feature | Unsupported version refused | Old hub and old daemon errors map to unsupported |
| 12 | Recipe policy | Digest mismatch and parameter validation | Recipe file must be root-owned; secret env names refused; one parameter = one argv element; network deny needs its feature | Raw argv is never sent |

## 13. Known limits

- Memory and disk bounds are sampled on macOS, not kernel-enforced.
- `dedicated_slot` cannot exclude a GitHub job that starts later; true exclusion needs `dedicated_host` or a future
  shared host lock that both executors honour.
- `sandbox-exec` is deprecated. If it is unavailable, no network-deny recipe is offered.
- Build code runs with network access when a recipe allows it. The trust boundary is the grant and recipe review.
- With 512 KiB chunks, large sources take many calls, bounded by limits and rate limits.


## 17. K0R security amendment — contract revision 1.1 (normative)

This section replaces conflicting v1 draft requirements above; all other requirements remain. Wire protocol is
still integer `1`, but **contract revision is `1.1`**. Nothing implementing the draft alone may execute a job.
These are implementation requirements, not claims that a worker, sandbox or live grant is already deployed.

### 17.1 Principal confinement on every surface (R1)

Worker classification is a permanent, local-admin-created `worker_principals` tombstone, separate from enabled
worker registrations. Disabling/removing a registration never restores ordinary client authority. Register a NEW,
otherwise unused client identity only; refuse an existing pod, operator, normal client or host identity. Keep its
`mcp/clients` membership. Pre-register the reserved invite node as a worker principal BEFORE handing the invite
to the worker; on failure revoke the unused invite. No enrollment-to-registration privilege window is permitted.
The approval-gated install must never reclassify the mini's existing client or pod.

The exact worker RPC allowlist is the set of operations enumerated in §9, not a prefix wildcard. Enforce it before
any alternate dispatch. Deny worker principals on every non-workspace authenticated surface, including Iroh
audio/video ALPN handlers, HTTP media/provider proxy endpoints and authenticated update downloads. HTTP `/rpc`
uses the same exact RPC allowlist. Check at admission and again after awaits; close any existing unauthorized
stream when classification or revocation changes. Unreadable worker-policy state denies worker enrollment and
workspace operations; it must never classify an uncertain identity as an ordinary client. Persist the permanent
classification in a separately readable, durable policy index so workspace DB maintenance cannot erase it.
Ordinary non-worker clients, pods, their credentials and capabilities retain their existing rules.

### 17.2 Mandatory build confinement and trusted evidence (R2, R3)

Reviewed build commands execute repository-controlled code; an argv allowlist is NOT a sandbox. Every recipe,
including `network:"allow"`, requires `isolation.profile.v1`. A separate UID/system LaunchDaemon alone does not
prove silence, lack of TCC access, or confinement. Apply a root-owned, inherited default-deny OS confinement
profile to every build descendant before any repository code runs. Allow only explicit toolchain reads and
job-owned writable `src/home/tmp/out`. Deny supervisor keys, journals, mirrors' credentials, other jobs, user
homes, keychains, GUI/WindowServer, camera/microphone/screen/audio interfaces, local control sockets and
unapproved Mach/XPC services. Deny scheduler/persistence registration (launchd, cron, at, login services),
privilege/UID changes, mount changes and signaling/tracing the monitor. No build process inherits supervisor
transport/control FDs; close all descriptors except explicit stdin/stdout/stderr. Allow-network means only
recipe-declared egress via a trusted restrictive proxy, not unrestricted loopback, LAN or metadata access.
Add recipe `egress_policy_digest` (null for deny); grants pin this policy through the recipe digest. No-network
recipes additionally require `network.deny.v1`. No verified sandbox/proxy means no applicable recipes advertised
and no execution, not a fallback to unsandboxed execution.

The supervisor or a protected monitor under the supervisor UID owns the journal, control endpoint, exit evidence,
log spool and all parent directories. Only the data subdirectories are slot-owned. Never trust `<job>/exit.json`
or a socket writable by build code. The fixed-argv launcher must deliver exit status through a private channel
not inherited by the build, and its monitor must be unsignalable/untraceable by build descendants. Lost or forged
monitor evidence is `unknown_outcome`, never success. The monitor survives supervisor reconnect and maintains
deadline, output and authorization watchdogs independently. No pid/start-time match authenticates a writable
status file. Exact sudoers argument constraints and root-owned helper parents are mandatory: a bare path rule
alone allows arbitrary arguments. Helpers accept only validated job IDs/slots and supervisor-owned descriptors,
never arbitrary paths, argv, environment or deletion targets.

UID reap plus two empty enumerations is accepted ONLY with verified persistence denial and no external service
able to restart work for that UID. Hold the local slot lock across launch, reap, artifact collection, cleanup and
acknowledgment. Account inventory must prove no login, scheduled jobs or other executor uses the slot UID.
Reap helpers themselves must have exited before empty enumeration. On any uncertainty quarantine the slot and
keep its hub reservation. Never reuse a UID while a previous job's directory is retained: securely remove the
old writable tree, using descriptor-relative no-follow traversal, before releasing the local slot, or allocate a
fresh UID. Artifact retention is in the supervisor/hub store, not a tree accessible to the next build.
`reap_failed` may be displayed as failed, but is not capacity-releasing or retry-eligible until reconciliation.

macOS target selftest MUST exercise forbidden file reads, supervisor evidence forgery, parent symlinks, signal
attempts, loopback/Mach access, scheduler registration, audio/device operations and sandbox inheritance through
`setsid`. Use synthetic canaries and mocked/reversible interfaces; no real household actuation or prompts.
A requested build that cannot run within this boundary is unsupported, not grounds to relax existing accounts.

### 17.3 Complete source validation and export policy (R4)

Validate the ENTIRE base tree and final tree for BOTH source kinds before checkout, not just patch headers or
untracked paths. Reject symlink/submodule modes anywhere, path/component case-fold collisions, file/directory
prefix collisions and all prohibited names. Enforce file/count/expanded-byte limits on the resulting tree too;
a small binary patch cannot expand past them. `files` remain selected untracked additions; do not silently
include unrelated files. Canonical manifests and patches describe the exact exported bytes.

Remove the tracked-file exception to secret exclusion: previously tracked does not mean safe to export. Apply
path policy to base and changed files equally. `.env.example/.sample/.template` are merely path exceptions,
never content-scan exemptions. Repository `.gitleaks.toml` may ADD rules, never disable the trusted baseline.
Scan final decoded file bytes (including binary patch output), not only added diff lines. K3 must complete this
before uploading; K2 repeats it before execution. K1 scans uploaded blob/patch bytes using the same mandatory
baseline and rejects definite hits, but cannot attest the remote base tree. Unknown scanner errors fail closed.
This is defense in depth, not a guarantee that arbitrary secret text can be recognized; export only files within
the recipe's admin-reviewed repository scope. No credentials are included to make a build work.

K3 reads selected files through opened directory descriptors with no-follow on EVERY component; validate fstat
on the opened file and hash/read those same bytes. Reject unstable reads and symlink/hardlink substitutions.
K2 materializes from a temporary, isolated Git object/index directory. Never checkout with repository/user Git
filters, hooks, credential helpers, global config, attributes-driven transformations or external protocols active.
Prefer reading validated raw blobs into new files using descriptor-relative exclusive creation: `write-tree`
is the source identity, not permission to invoke checkout filters. Fetch happens only in a supervisor-controlled
mirror with a fixed admin remote, isolated config and credentials; no build process can read that credential.
Materialization hashes local written bytes against each blob. K3 uses the same filter-free semantics.
Artifacts likewise use descriptor-relative traversal with no-follow on ancestors and leaves, reject hardlinks
(`nlink != 1` for regular files), devices and links; stop all build writers before collection. Safe tar extraction
must reject all link types, duplicate/prefix-collision paths, oversized expanded data and preexisting symlink
parents. An `O_NOFOLLOW` flag on the final path alone is insufficient.

### 17.4 Crash identity, fencing and receipt completeness (R5)

Worker hello adds `{hello_id, instance_id}` (worker-generated UUIDs, durable before send). Same hello_id and same
canonical payload returns the SAME session without minting a new one; changed payload conflicts. A new hello
for the same instance may reconcile its journal. A different instance cannot fence a live instance or adopt its
jobs: return `stale_session`/hold until the prior local monitor is proven stopped and admin recovery authorizes
replacement. Local singleton/slot locks are necessary even after a network partition.

Journal `claim_id` before claim and `launch_intent` before spawning. If the hub committed a claim but the reply
or journal was lost, recover the same assignment; never claim or launch anew. An uncertain launch intent is
reconciled with the protected monitor; absent proof means unknown and reap, NEVER spawn. Persist once-only
launch acknowledgement in the monitor before allowing repository code to execute.

Replace the draft's "fence increments on every state change" with an immutable `execution_fence` per assignment
(the wire field remains `fence`), plus monotonically increasing `status_revision` for observation. Cancels/events
do not invalidate a legitimate finish's execution fence. Every worker job operation, including all artifact
operations, requires `{session_id, job_id, fence}` and verifies authenticated worker, exact slot, lease ownership
and valid phase. Claim idempotency is scoped to worker+claim_id with its frozen slot/args, and survives session
renewal. Adopted jobs retain the same execution fence. Replaying an operation never bypasses current authority
or exposes another principal's data. Unknown outcome is not a terminal/retry-eligible state. `release-slot` retires
the execution fence permanently, records manual verification and an immutable failed/admin_reconciled receipt;
late writes/finishes using the retired fence cannot affect the next occupant. It never requeues the attempt.

Successful finish must match the frozen source/recipe/parameters, verified tree, log digest and committed artifact
set. Cancel/finish races serialize: if cancel committed first, finish cannot be succeeded or publish artifacts;
if finish committed first, cancel is a no-op returning that terminal receipt. Purge staged artifact visibility
on cancellation. If cleanup is unproven, report unknown or quarantine, not `cancelled`.

`workspace.get` and every terminal JobStatus return `receipt` (the FULL canonical receipt object) alongside
`receipt_digest`; pending status uses null. Add to receipt: `contract_revision`, `required_features`,
`execution_fence`, `protected_ref_reachable` (boolean), `admission_mode`, `isolation_policy_digest`, and the frozen
effective budget. These are wire extras, not a change to Phase A's state enum. The receipt is immutable; any
admin reconciliation creates the first terminal receipt rather than modifying a prior terminal receipt.

Retain compact dedup tombstones (principal, IDs, args_digest, terminal receipt digest, retired fence) indefinitely,
even when blobs/logs and full audit expire. A request tombstone returns a bounded expired result, NEVER not_found.
`not_found` is safe only within the same authenticated principal and non-rolled-back ledger. Bind the client to a
hub `ledger_epoch` exposed by hello; restore from backup/ledger loss rotates it and holds all workers pending
reconciliation. K3 does not resubmit an uncertain intent across an epoch change. Add `ledger_epoch` to submit
and freeze it into the args digest. Pre-admission refusals write audit only, not request/slot reservations.

### 17.5 Revocation, authorization expiry and safe drain (R6)

A separate workspace DB transaction does not atomically protect reads from the enrollment database. Reuse the
existing `enrollment_lock.shared(root)` around the final role/revocation/generation check and the short workspace
transaction; lock order is enrollment lock, then workspace transaction. Never hold either across polling or blob
I/O. Recheck after awaits and before publishing reads or committing mutations. Workspace grant changes serialize
in that same workspace transaction. Stamp submitter and worker enrollment generations; generation drift requires
reconciliation, even after revoke/allow. Re-read current client membership as well as revocation. Do not modify
existing node authority, HubCommandPolicy or the main hub DB schema to implement this.

Claim and heartbeat recheck grant EXPIRY as well as revocation, submitter enrollment and worker enablement.
The hub grants a short execution authorization on claim/heartbeat: `{authorization_seq, valid_for_ms:15000}`
per active job. Monotonic local expiry is measured from the START of the corresponding RPC, not response receipt;
ignore a reply that has already expired or has a stale sequence. The protected monitor must terminate/reap when
this authorization expires, when a cancel arrives, or on authentication refusal (`forbidden`, `unauthorized`,
transport enrollment loss), whichever comes first. Connection failure is not an authorization renewal. No
claim-based launch without a still-valid authorization. A stale-session response fences new effects and starts
local safe drain unless same-instance reconciliation proves current ownership. Persistent network loss therefore
has bounded kill initiation (15 s), followed by the existing 10 s TERM grace and bounded reap, not 900 s of
unchecked execution. The 900 s hub timeout remains an observation deadline, not execution permission.

Feature-off is an admission switch, NOT a block on safety operations: refuse new sources/submits/claims, cancel
queued jobs and request cancellation of active jobs. Keep authenticated owned get/logs/cancel/receipt/artifact
reads and worker drain/finish operational for valid principals. Expired/revoked grants cannot start work, upload
new source or renew execution; an otherwise enrolled original owner may still observe/cancel its admitted jobs.
Revoked node credentials remain refused; admin status/reconciliation is the recovery route. Disabled worker
registrations keep their permanent restriction; admin may enable a cleanup-only session with no claim/source
access for reconciliation, otherwise the hub holds unknown slots pending manual verification.

### 17.6 Admission, budgets and host overlap (R7)

Admin registration stores the full canonical recipe definitions and independently verifies their digests; hello
cannot substitute caller-provided recipe policy. Admin registration, never worker hello, fixes max slots, slot UIDs,
repository/recipe digest allowlist, host
binding, admission mode, maximum budgets and isolation policy digest. A worker may advertise a subset of these
but cannot add slots or alter them. Grants pin exact `{recipe_id,recipe_digest,repository_id}` tuples; a same-ID
recipe revision needs a new admin grant. Bind source.repository_id to recipe.repository_id on every submit.
Reject unsupported/negative/zero/overflow budgets and inconsistent deadline fields. Effective budget is the
minimum of requested, recipe, grant and registered-host maxima and is frozen in status/receipt/claim. Admission
reserves aggregate CPU and memory, free disk headroom and per-slot disk bytes, not merely a job count. Add
registered `disk_bytes_per_slot` (not caller-controlled) and include it in the effective wire budget. The local
monitor independently enforces the granted limits. The two deadline fields must agree; set `deadline_at` at
admission and derive a monotonic remaining duration once, never extend it on reconnect/restart or clock change.

Initial v1.1 live execution uses `dedicated_host` only: a separately opted-in build host without a competing
GitHub/other build executor. `dedicated_slot` remains a displayable advisory inventory value but is NOT execution
eligible in this revision. Never stop/unregister existing GitHub runners or remove mini tools to manufacture
exclusivity. If the approved target cannot provide exclusive build capacity, fail closed and obtain an approved
dedicated target; a future shared-lock implementation for both executors needs a separate reviewed amendment.
Process-name observations alone do not prove mutual exclusion. Host provisioning evidence is required, and an
unexpected competing executor causes hold and local drain, not a claim of preserved exclusivity.

Memory/disk sampling and QoS remain best effort, not hard host isolation. Bound and reserve worst-case output and
storage before admission, keep headroom, and fail closed when a platform cannot enforce the recipe's required
resource limits. Log capture must be via protected monitor pipes with continuous draining and bounded disk spool;
dropping bytes after output_bytes must not allow the local log file to grow without limit. Apply bounds again
after UTF-8 replacement/redaction. Use streaming redaction with carry across chunk boundaries; it is not a
substitute for excluding secrets from source and environment.

### 17.7 Downgrade prevention and interoperable encoding (R8)

Hello adds `contract_revision:"1.1"`, `ledger_epoch`, and feature `security.profile.v1`. This feature means ALL
requirements in §17, not a best-effort subset. Mandatory baseline for submitter, hub and worker is
`security.profile.v1`, `source.snapshot.v1`, `source.commit.v1`, `logs.cursor.v1`, `artifacts.sha256.v1`,
`resume.v1`, `receipts.v1`, plus `isolation.profile.v1` and a supported cancel mechanism on the worker/hub.
Network-deny additionally needs `network.deny.v1`. Missing ANY mandatory feature refuses execution regardless of
what a recipe's optional features say. Submit adds `protocol_version:1`, `contract_revision:"1.1"` and sorted
`required_features`; all are frozen into the ledger/claim/receipt. Hub validates them independently of hello and
worker rechecks immediately before launch. Replay of an old proposal cannot silently strip safety requirements.
An older runtime remains usable for its existing device operations, never as a shell/script fallback.

Keep the restricted ASCII manifest encoding and existing vector unchanged. For ALL other digest-bearing objects
(args, recipes, receipts) use canonical JSON: recursively lexicographically sorted ASCII keys, no whitespace,
array order preserved except schema-specified sorted sets, true/false/null lowercase, integers signed base ten
with no leading zeros or negative zero, range [-2^53+1,2^53-1], no floats. Strings must be valid Unicode scalar
sequences; output UTF-8 without normalization, escape quote/backslash, and encode U+0000..001F as lowercase
`\u00xx` (including LF/TAB, not short escapes); do not escape slash or other characters. Reject duplicate JSON
keys before decoding and reject lone surrogates. Boolean is not accepted where an integer is required. K1 owns
cross-language golden vectors for negative exit codes, nulls, booleans, quotes, backslashes and Unicode; K2/K3
consume them. The manifest's no-escape constraints do NOT apply to arbitrary recipe regexes or toolchain strings.

### 17.8 Availability and quiet/stop controls (R9)

Workspace traffic shares transport/RPC resources with voice even with a separate SQLite DB. `worker.claim` in
v1.1 returns immediately (`wait` must be 0; limits.claim_wait_max_seconds=0); workers retry with jitter, never
occupy shared RPC workers with 20 s polls. Reserve admission/storage quotas and bounded chunk concurrency for
workspace operations. No large hashing, scans, archive generation or synchronous fetch in shared RPC handlers;
use bounded dedicated workspace tasks, expose pending upload/verification state, and keep source unsealed until
verification completes. Control heartbeat/cancel/drain gets priority over bulk traffic. K1 must test existing
status/voice admission latency under maximum permitted chunk and claim load; no unbounded workspace queue.

Workspace hold is not pod control.stop. Document the separate controls explicitly; do not imply that pod stop
cancels builds. Never auto-resume a pod, change quiet/listening/mic settings or emit sound. The verified mandatory
sandbox, not absence of a GUI login, is the reason a build cannot bypass quiet/stop through media, IPC or GUI.
If that boundary or reserved headroom fails selftest, do not enable workspace execution on that host.

### 17.9 Required new regression gates and file ownership

These gates ADD to the earlier matrix; conflicting old tests must assert the corrected semantics, not retain an
unsafe expectation. Each name is an acceptance test to IMPLEMENT, not evidence that it already passes.

| Finding | Hub K1 | Worker K2 | Client K3 |
|---|---|---|---|
| R1 | `test_worker_denied_media_alpn_http_and_after_disable`, `test_worker_preregistered_before_invite`, `test_policy_unavailable_no_identity_fallback` | `TestWorkerCredentialCannotReachMedia` | `test_existing_device_identity_unchanged` |
| R2 | `test_isolation_feature_required_for_all_recipes` | `TestSandboxDeniesAmbientAuthorityAndPersistence`, `TestSlotCannotForgeMonitorEvidence`, `TestSudoHelperRejectsArbitraryArgs` | `test_no_unsandboxed_fallback` |
| R3 | `test_reap_failure_holds_capacity_and_retry`, `test_admin_release_retires_fence` | `TestSchedulerDeniedBeforeReap`, `TestNoUIDReuseBeforeCleanup`, `TestSetsidNoDelayedWrites` | `test_unknown_not_retry_eligible` |
| R4 | `test_tracked_secret_no_exception`, `test_upload_scan_mandatory` | `TestBaseTreeSymlinkRejected`, `TestBinaryPatchExpansionBounded`, `TestNoGitFilters`, `TestArtifactAncestorSymlinkAndHardlinkRejected` | `test_repo_scanner_cannot_disable_baseline`, `test_untracked_open_race_rejected`, `test_final_bytes_scanned_before_upload` |
| R5 | `test_hello_lost_reply_same_session`, `test_cancel_finish_race`, `test_every_worker_write_fenced`, `test_tombstone_never_readmits`, `test_full_receipt_returned` | `TestCrashAtEveryLaunchBoundaryNeverRespawns`, `TestDifferentInstanceCannotAdopt` | `test_ledger_epoch_change_holds_uncertain_submit`, `test_verify_full_receipt` |
| R6 | `test_enrollment_lock_revoke_race`, `test_expiry_cancels_running`, `test_feature_off_keeps_drain`, `test_generation_change_not_resumed` | `TestPartitionAndUnauthorizedExpireAuthorization`, `TestDelayedHeartbeatCannotRenew`, `TestMonitorWatchdogSurvivesSupervisorDeath` | `test_expired_grant_owner_can_observe_cancel` |
| R7 | `test_worker_cannot_expand_slots_or_recipes`, `test_aggregate_budget_reservation`, `test_source_recipe_repository_match`, `test_dedicated_slot_ineligible` | `TestBoundedLocalLogSpool`, `TestDeadlineNeverExtends`, `TestCompetingExecutorHolds` | `test_advisory_slot_not_execution_candidate` |
| R8 | `test_mandatory_features_cannot_be_omitted`, `test_canonical_extended_vectors` | `TestMandatorySecurityProfile`, `TestCanonicalExtendedVectors` | `test_no_safety_downgrade`, `test_canonical_extended_vectors` |
| R9 | `test_workspace_load_preserves_control_latency`, `test_claim_never_blocks_rpc_pool` | `TestQuietStopUnaffectedWithDeniedAudioIPC` | `test_workspace_hold_not_pod_stop` |

K1 additional owned paths: `hub/server/workspace_jobs/principals.py`, `hub/server/workspace_jobs/maintenance.py`,
`hub/tests/test_workspace_jobs_transport.py`, `hub/tests/test_workspace_jobs_load.py`, and targeted worker-only
guards in `hub/server/http_server.py` and `hub/server/iroh_server.py`. Reuse `enrollment_lock.py` read-only.
Maintenance is a bounded workspace-owned task/service (started through the normal hub lifecycle); it performs
verification/expiry/reconciliation without a shared-RPC blocking loop. K1 documents its lifecycle and tests clean
shutdown/restart. K2 additional owned path: `client/mcp/internal/workspace/isolation/` and root-owned sandbox
policy/service assets under its existing service directory. K1 owns shared canonical/security-profile/transport
fixtures. K2/K3 consume them read-only. Existing K1→K2 serialization remains. No changes to pods, Swift device
code, HubCommandPolicy, NativeMCP, or existing mini privileges are authorized.

### 17.10 Changelog and disposition

Revision 1.1 supersedes the pre-review v1 design: permanent all-surface worker confinement (R1); mandatory sandbox
and protected evidence (R2); persistence-safe reap/UID reuse and quarantine (R3); full-tree/secret/path validation
(R4); crash/fence/receipt/tombstone semantics (R5); bounded revocation and drain (R6); admin-pinned aggregate
admission and dedicated-host rollout (R7); mandatory security negotiation and canonical JSON (R8); bounded shared
transport load and truthful quiet/stop semantics (R9). No high/critical finding is left without a normative
contract resolution. Implementation and live proofs remain mandatory downstream; this review does not certify
an unbuilt worker or authorize an install, credential, grant, production cancellation or household action.
