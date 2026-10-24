# Workspace-job wire contract v1 (proposed)

Status: frozen design for the workspace-job provider, intended for review and implementation. The
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
