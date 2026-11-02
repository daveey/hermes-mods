# project-work

A host-side workflow controller for one configured Slack project. It exposes only
`project_work_request` and `project_work_status` to the agent. Repository, profile,
board, project, channel and session targets are private policy, never model arguments.

## What is implemented

- Authenticated native Bolt event observation, request-local identity proof without
  environment-variable fallback, strict request/status schemas, owner-only plan approval.
- SQLite WAL/FULL request ledger, unique inbound identities, cross-process locking,
  generations, native session/task links, evidence and exact delivery receipts.
- A visible top-level native project conversation; the planning prompt must be read
  back from persisted history before any Kanban card is created.
- Concrete plan confirmation for every generation, including owner requests. Approval
  binds the plan hash and generation; followups invalidate approvals and evidence,
  retire old work through a verified native adapter, and retain old delivery obligations.
- Native `session.create`, `prompt.submit`, `session.resume`, `session.archive` bindings
  and owner-conversation `kanban_create` tool calls, not parallel session/task stores.
- Implementation → independent review → release graph; closeout checks prior outstanding
  generations, exact-head tests/checks/review/delivery facts, writer and approval state.
- Original-thread Slack delivery, deterministic message key, paginated exact readback,
  latest-inbound check, then exact-session archival and a second check. A late change
  rolls the archive back. No session or transcript deletion is implemented.
- Restart reconciliation and exact-effect recovery. An unprovable prompt or send is
  retained as an error, never blindly retried. Failed work remains visible.
- Out-of-order input keeps a monotonic watermark and explicit incorporated-message
  membership. Uncertain card recovery uses durable owner IDs, not expired runtime IDs.

This is a transport-independent plugin, not an installation of a particular workspace.
A deployment must supply the native transport/readers described below. Unit tests do
not establish live Slack delivery, project sidebar ownership, worker isolation or CI.

## Private configuration

Create `project-work.json` under the source profile home, owner-owned and not
 group/world writable. All keys shown are required; unknown keys are rejected.
Paths must be absolute and resolved, not symlink aliases.

```json
{
  "version": 1,
  "policy": {
    "team_id": "TEXAMPLE",
    "channel_id": "CEXAMPLE",
    "ingress_profile": "project-assistant",
    "builder_profile": "project-builder",
    "reviewer_profile": "project-reviewer",
    "board": "example",
    "project": "p_example",
    "repo": "example/project",
    "cwd": "/srv/projects/example",
    "owner_ids": ["UEXAMPLE"]
  },
  "runtime_socket": "/srv/hermes/project-assistant/project-work/runtime.sock"
}
```

The socket must be immediately under `<profile home>/project-work`. That directory
must be mode 0700; the server creates a mode 0600 socket. The ledger is fixed at
`<profile home>/project-work/ledger.sqlite3`. Never mount this directory, the source
profile, host credentials, host home or Docker socket into a builder sandbox.

Provision the project with native `projects_db`/project APIs, use its local ID and
bind its dedicated board before activation. Create isolated builder/reviewer profiles
and task workspaces separately; this plugin does not grant terminal or general Kanban
tools to the Slack assistant. Publication credentials belong in a narrowly scoped
host-side broker boundary, never in model-visible arguments or forwarded environment.

## Supervised runtime and native binding

```sh
python plugins/project-work/runtime.py --home /srv/hermes/project-assistant \
  --adapter /srv/hermes/private/project_binding.py
python plugins/project-work/runtime.py --home /srv/hermes/project-assistant --reconcile
```

The first command is a long-running service; supervise it independently of any Desktop.
It reconciles every 30 seconds by default (`--interval`). The second contacts the
existing local runtime and requests one reconciliation. Do not start competing runtimes.

The private binding exports `build_ports(policy, NativeBridge)` and returns an instance:

```python
def build_ports(policy, NativeBridge):
    return NativeBridge(
        policy, rpc=native_rpc, owner_tool=owner_conversation_tool,
        sessions=read_native_sessions, cards=read_native_card,
        slack=native_slack_web_api, evidence=refresh_completion_facts,
        bot_user_id=configured_bot_user_id,
        projects=read_owner_project, card_keys=read_all_card_keys,
        quiesce=retire_native_generation,
    )
```

These names denote deployment-supplied real capabilities, not mock implementations.
The adapter file is trusted executable host configuration, current-UID owned and not
 group/world writable. Do not load it from a repository/task-writable directory.

### Binding contracts

- `rpc(method, params)` returns an unwrapped native RPC result. Preserve lease/owner
  checks; never take over unrelated sessions. Both create and resume return live
  `session_id` and durable `stored_session_id`. Creation idempotency is not assumed
  durable across gateway restart. Recovery searches the unique workflow title and
  resumes that exact stored ID; no substitute conversation is created.
- `sessions(query)` returns normalized persisted rows. Query selects `id`, `title`,
  or `runtime_id`, plus `profile`. Required row fields: `id`, `source`, `cwd`, `title`,
  `profile`, `parent_session_id`, `archived`, `busy`, `pending_approval`, `messages`.
  `messages` includes native role/content; runtime IDs must be resolved through the
  owning gateway, never guessed. Absent/unknown state is not false.
- `owner_tool(name, arguments, live_session_id)` executes the native `kanban_create`
  TOOL under the workflow owner's conversation, preserving native session subscription
  and durable idempotency. Do not substitute private handlers, raw SQL or Kanban CLI.
  Returns native JSON with `task_id`, `id`, or `task.id`. Tool arguments use `project`
  and `parents`, not private schema names. Worker assignee/scope comes from policy.
- `projects({project, profile})` returns native `Project.to_dict()` augmented with
  trusted `profile` and git-remote-verified `repo`. ID/slug, `primary_path`,
  `board_slug` and `archived: false` must match policy in the owner-tool profile
  BEFORE creation. Keep this mapping immutable while the controller is active.
- `card_keys({idempotency_key, board, profile, include_archived: true})` returns ALL
  matching IDs, including archived. Ambiguity fails closed. Uncertain create intents
  only recover; they never invoke `kanban_create` again on a missing lookup.
  Intents persist `stored_session_id`; legacy intents resolve the unique workflow
  title in the fixed builder profile. Recovery validates scope without resuming a
  session or requiring the old runtime ID to exist.
- `cards(id)` returns native Task fields plus trusted `board` and ordered `parents`.
  Readback validates `project_id`, `assignee`, durable `session_id`, `idempotency_key`,
  `workspace_kind: worktree`, `workspace_path: <cwd>/.worktrees/<id>` and
  `completion_contract` as well as marker/parents.
  The implementation/review/release cards carry a unique workflow/generation/stage
  marker. The reader must retain native run provenance for the evidence collector.
- `slack(method, payload)` uses the configured workspace's native bot client; it returns
  Slack's `ok` response, not a synthesized receipt. Needed methods are
  `chat.postMessage` and `conversations.replies`. Required scopes and membership must
  be checked during deployment. No home-channel fallback is used.
- `evidence(workflow)` refreshes the facts below from native task runs, current GitHub
  head/checks, independent review and actual release/delivery. Never relay a model's
  assertions as verified facts. The public bridge validates the facts' relationships;
  the private reader owns provenance and freshness.
- `quiesce(workflow, old_generation)` is a mandatory native retirement boundary.
  Revoke old-generation publication, stop active workers, and park pending native
  cards using supported native operations. Resolve ALL old stage idempotency keys,
  including lost-create responses and archived cards. Return freshly verified
  `{workflow_id, generation, publication_revoked: true, live_writer: false,
  pending_dispatch: false, idempotency_keys: [...]}` with the exact old key set.
  No callback or incomplete proof blocks the new prompt/graph. A model promise is
  not proof. The private publication broker must check current generation/approval
  and the latest Slack thread immediately before every publish; credentials must
  never be retained in workers. Retirement cannot revoke a credential already leaked
  into a worker, nor undo an external publication. Prove this boundary during rollout.

Evidence fields: `repo`, `workflow_id`, `generation`, `head_sha` (40 lowercase hex),
`implementation_run_id`, `review_run_id` (nonempty and different), `review_head_sha`,
`review_approved: true`, nonempty `tests: [{sha, ok: true, command}]`, nonempty
`required_checks: [name]`, `checks: [{name, sha, status: "completed", conclusion: "success"}]`,
`delivery: {sha, verified: true, receipt}`, `pending_followups: false`, `live_writer: false`.
Every SHA must match current head. Missing, duplicate, stale or pending required checks
fail. If the repository has no protected checks, enumerate the agreed CI set explicitly.

### Planning, completion and trusted host operations

The model tools cannot approve, propose plans, close out, publish or archive. Native
middleware supports `!project request <text>` and owner-only
`!project approve <workflow UUID> <full plan hash>`. Normal human replies in an existing
workflow thread are required followups, including messages without a mention and
`file_share`, `thread_broadcast`, `me_message`. Bots are not new authority; external
Slack Connect `user_team` values are rejected. Root turns accept either the native
reply-in-thread root timestamp or no thread binding. Status is thread-local or owner-only.
New workflows must start in a root channel message. A request in an already-owned
workflow thread is a followup; a request in an unrelated existing thread is rejected.
Captionless human messages become an explicit non-text followup requiring inspection
of the original message/attachments, identically in live ingress and backfill.
Even "status?" or "thanks" in an owned thread is new work: it revokes the old plan
and reopens a completed session. Explain this conservative UX during deployment.

Middleware appends to a private `ingress.sqlite3` journal without making network calls
and always calls native `next()`. A background worker retries uncertain deliveries.
Deterministic invalid requests are acknowledged and retained in the journal's
`rejected(payload, reason, rejected_at)` table, not retried forever. Transport failures
remain pending. The journal is independent of the controller effect lock; network stalls do not block
Bolt acknowledgement. Every `message`/`app_mention` event the middleware sees leaves one
`Project ingress ...` log line, including events outside the project channel
(`outside project channel`) and other bots' posts; other event types log at debug.
The bot's own posts never reach it: slack_bolt's built-in self-event filter drops them
before plugin middleware (`tests/test_ingress_observability.py` drives a real `AsyncApp`).
`project_work_request`/`project_work_status` refusals return `{"ok": false, "error":
<type>, "message": <reason>}` naming the project channel, so the model can tell the
requester what to do. Register refuses non-sandboxed terminal/code backends, a Docker
cwd mount, Docker extra args other than `--runtime=runsc` (gVisor), and any Docker volume
except a read-only single regular file `<profile home>/sandbox-bin/<file>:/usr/local/bin/<name>:ro`
(no symlinks, owned by the gateway uid; tests in `tests/test_ingress_security.py`). Verify actual implicit mounts and profile routing in deployment.
Reconciliation backfills active threads and approval attempts. Successfully archived
threads are checked at most once per five minutes for the first 24 hours, then only
authenticated live ingress reopens them. Old ledger archives without `archived_at`
are not polled. Deployments needing catch-up after longer gateway outages must replay
their durable authenticated event journal; this is not an indefinite Slack history scan.
A transport/read outage records an error but preserves a previously verified archive.
Positive source/approval/receipt changes reopen it as `invalidated`, retaining the old
completion evidence; the controller never compares that historical delivery to a
later repository HEAD. A fresh human followup creates a new plan generation. Exact
source edits/deletions still require operator adjudication, not silent acceptance.
Unchanged awaiting-approval sessions are not resumed each tick.

A trusted owner-conversation/runtime binding collects the concrete planning response,
then calls local `propose_plan(workflow_id, plan_text, generation)`, posts the plan/hash in the source
thread and awaits the owner's exact approval command. Host callers use `Client.call`
with named arguments. After all graph stages finish, call
`closeout(workflow_id, result_text)`; the runtime durably owns retries after this point.
Missed task-completion triggers are reconciled from freshly verified native evidence:
the controller posts a deterministic receipt naming the repository and completed cards.
It does not invent a work summary from task prose. Planning proposals still require
the deployment's native planning-response hook/controller.

Local operations are `request(context, text)`, `status(context, workflow_id)`,
`approve(context, workflow_id, plan_hash)`, `observe_followup(context, text)`,
`propose_plan(workflow_id, plan_text, generation)`, `closeout(workflow_id, result_text)`,
`publication_gate(workflow_id, card_id)`, and `reconcile()`.
The read-only publication gate is for the host broker, without fabricated AuthContext.
It returns `{current_generation, approved, card_in_current_generation}` after fresh
approval and complete thread readback. Exceptions are denial, not permission. Both
booleans must be true and the generation must match the broker's intended operation.
This is a point-in-time check, NOT a transferable authorization token or lease: the
private broker must serialize its check-and-publish with generation retirement,
recheck immediately before publishing, and never hand credentials to workers.
The socket is a trusted host capability, not a remote API. Its filesystem boundary is
part of the deployment security model; all same-UID host code is trusted. Never expose
it through an unauthenticated proxy or model terminal.

## Failures and recovery

Inspect status `state` and `error`. `create_intent`, prompt `intent` and `post_intent`
are persisted BEFORE effects. Recovery requires proof of exactly the intended effect:
unique native title/ID, exact user prompt marker, or original-thread bot message with
matching deterministic `client_msg_id` and text. No proof means no replay and no archive.
Native empty drafts are not necessarily persisted: a lost create response before the
first prompt requires operator reconciliation. It is deliberately not retried blindly.
An archive intent carries a durable compensation obligation; on restart, unarchive
and verify visibility BEFORE checking evidence or thread watermark. Failed unarchive
readback retains the obligation. Deleted/edited owner approvals cannot authorize archive.
Never manually change ledger status to `archived` or fabricate delivery receipts.

### Native card-create refusal (`create_refused`)

`kanban_create` can refuse definitively, e.g.
`{"error": "Assignee 'X' lacks required capabilities: host_repo", "code": "assignee_capability_mismatch"}`.
`NativeBridge.create_card` raises `NativeCreateRefused` (`code`, `error`,
`idempotency_key`) only when the result is a dict with no task ID, a non-empty
string `code` and an `error` field, AND a fresh archived-inclusive `card_keys`
lookup afterwards finds no card. If that lookup finds a card it is validated and
adopted. Any other shape (no code, non-dict) or a failing lookup stays a plain,
ambiguous `RuntimeError`.

The stage loop then stores the entry as
`{'state': 'create_refused', 'params': <unchanged>, 'refusal': {code, error, idempotency_key, at}}`,
sets row `error` to `card_create_refused:<code>` and stops (later stages are not
attempted). Each later normal attempt (reconcile tick) first runs the read-only,
archived-inclusive `recover_card` and adopts a card it finds; otherwise it
re-persists `create_intent` (keeping the refusal history) and calls `create_card`
with the SAME params and idempotency key. Success sets `created` and the ID; a
repeated refusal moves the previous `refusal` into the `refusals` list, which
keeps the last 20 entries (a still-refused stage is retried once per tick). Params
and keys are never cleared or regenerated. An ambiguous outcome of that retry
leaves `create_intent`, which needs the operator method below.

When a newer generation retires an older one, a no-ID `create_refused` entry whose
archived-inclusive lookup (after quiesce) is still empty becomes `refused_retired`:
its last create was a proven refusal, so it is absent, not ambiguous. A no-ID
`create_intent` there still needs operator recovery. The deployed retirer (the
private integration's `GenerationRetirer`, wired as `quiesce`) treats such a stage
as retired-absent only when its fresh archived-inclusive lookup by the exact key
is empty; it still lists that key in the quiesce proof, so the bridge's key-set
check holds. A refused stage whose lookup finds a card is adopted and parked as
before, and a no-ID `create_intent` there still refuses as `ambiguous_card`
(integration `test_retirement`: `test_refused_stage_without_card_is_retired_absent_in_composition`,
`test_refused_stage_whose_lookup_finds_card_is_adopted_not_absent`,
`test_create_intent_without_card_stays_ambiguous_in_composition`).

### Operator recovery of a card `create_intent`

A `create_intent` with no ID (create sent, outcome unknown) is still ambiguous on
the normal path: `recover_card`, else `Ambiguous card creation; operator recovery
required`. Once the native cause is fixed (for example the assignee capability was
granted), a trusted operator calls `Service.recover_card_intent(workflow_id, stage)`
with the runtime stopped, or from the same process: it takes the store lock itself.
It requires an approved row, a stage entry in `create_intent` with no ID whose
params belong to the current generation, and, for `review`/`release`, a created
preceding stage matching `depends_on`; otherwise it raises `WorkflowError`
without writing. It runs archived-inclusive `recover_card` and adopts a found
card; on a miss it makes exactly ONE `create_card` with the same params. Results:
`created` + ID + `recovered_from: 'create_intent'`, or `create_refused` + refusal
evidence + `recovered_from: 'create_intent'`. Any other exception leaves the
stored entry byte-identical and propagates. It returns the updated entry and never
advances other stages or sends prompts; the next reconcile continues the graph.

### Operator procedure for an irrecoverable intent

There is deliberately no "force retry" or direct ledger-edit operation. If an
authoritative lookup proves an intent absent rather than recoverable, use this
quarantine-and-replace procedure (trusted deployment operator, not the agent):

1. Pause the dedicated project runtime and its ingress delivery worker under their
   supervisors. Keep the Bolt journal accepting events. Snapshot the ledger and
   journal with SQLite's backup API; retain them as the incident audit record.
2. Revoke publication for the old workflow at the private broker. Use native task
   operations to stop all its workers and park all dispatchable cards. Resolve the
   exact idempotency keys including archived cards. Confirm no in-flight native RPC,
   pending prompt, background writer, or outbound Slack request can still apply.
   A missing lookup alone does not prove an in-flight effect cannot arrive later.
3. Check the exact native session title/stored ID and prompt marker, all stage keys,
   and original Slack thread/result `client_msg_id`. Record the lookup evidence and
   native cancellation receipts in the private incident record. If an effect exists,
   restore the reader's exact mapping and let normal reconciliation recover it.
4. If the effect is conclusively absent, retain the old workflow as failed and visible;
   do not archive it, mark it successful, reset its intent, or reuse its keys. Announce
   the failed workflow ID and replacement reason in its Slack thread. Ask the owner
   for a new root request referencing the incident. It receives a new workflow/session
   and requires its own concrete-plan approval. Keep the old broker revocation in force.
5. Resume the supervisors and verify that only the replacement's new keys can dispatch
   and publish. The old row remains a visible audit record with its recovery error.

This procedure intentionally abandons the failed attempt rather than pretending it
completed. Native cancellation and publication revocation are deployment-specific
operations; the deployment runbook must name and verify them before activation.

Archive reads exact native identity, source, profile, cwd and title; Slack-origin or
parented sessions are refused. Native compression/lineage changes that cannot satisfy
these exact checks require explicit deployment reconciliation, not fuzzy title fallback.
An unavailable runtime/reader is an error, not evidence of completion. Followups reopen
an archived owned session and require a new plan confirmation. The platform's own
home-channel configuration is neither read nor changed by the plugin.

Gateway-local IDs are replaced on successful resume, including when only the gateway
restarts and the controller survives. Card recovery uses durable ownership; a new
card write uses the current validated live owner, never the first cached alias.
Only explicit `RejectedScope`, `RejectedRequest`, and missing-workflow approval
errors are terminal ingress rejections. Environmental/native PermissionError,
ValueError, KeyError and transport errors remain queued for retry. The deployment
must monitor the journal's `rejected` table and notify the original requester;
dead-letter persistence alone is not user notification.

Operational limits: invalidated/errored active rows still reconcile each runtime tick.
An edited source can remain invalidated even after a fresh followup; followups do
not erase the edited history. Use operator recovery, not automatic approval or archive.
The quarantine procedure above does not set a ledger quarantine flag: before resuming,
the private adapter MUST durably deny all future native writes/dispatch for abandoned
workflow IDs (not just publication). If that denial is unavailable, keep the runtime
paused; a late recovered effect must not restart an abandoned graph. Verify Slack
pagination against the real API before rollout, including repeated page-boundary IDs.
For Unix socket tests use a short `--basetemp` inside your workspace when the inherited
temporary directory is long; native AF_UNIX endpoint length limits still apply.

## Tests

Run from the repository root:

```sh
uv run --with pytest python -m pytest -q plugins/project-work/tests
```

Coverage includes scope rejection, invalid paths/owner types, plan approval and stale
hashes, duplicate requests across threads/processes, crash recovery, generation gates,
missing/stale exact-head checks, uncertain send, late followups, native method/parameter
contracts, Slack pagination, exact-session archive rollback, strict private config,
native ingress parsing, ingress log lines and tool refusal messages, real slack_bolt
`AsyncApp` dispatch through the plugin middleware (skipped unless `--with slack_bolt
--with aiohttp`), and a real local Unix-socket controller/client round trip.
Native RPC/Slack tests use deterministic fakes and are NOT live end-to-end proof.

`tests/test_create_refusal.py` maps the card-create refusal and intent recovery:

| Behavior | Test |
|---|---|
| Structured refusal + empty archived-inclusive lookup raises `NativeCreateRefused` (dict and JSON string) | `test_structured_refusal_with_empty_lookup_is_typed` |
| Refusal whose post-lookup finds a card returns the validated card | `test_refusal_whose_post_lookup_finds_card_returns_validated_card` |
| No/empty/non-str code, no error field, non-dict result stay plain `RuntimeError` | `test_unstructured_result_stays_ambiguous` |
| Post-refusal lookup raising, duplicate, or failing validation stays plain `RuntimeError` | `test_refusal_with_failing_post_lookup_stays_ambiguous` |
| First-attempt refusal persists `create_refused`, unchanged params, error, raises with code | `test_first_attempt_refusal_persists_create_refused` |
| Retry from `create_refused` creates with the same params/key, keeps history | `test_retry_from_create_refused_reuses_params_and_creates` |
| Retry adopts a card found by `recover_card` without creating | `test_retry_from_create_refused_adopts_recovered_card_without_create` |
| Repeated refusal appends to `refusals` | `test_repeated_refusal_appends_history_with_same_key` |
| Ambiguous retry re-enters `create_intent` and the operator path | `test_ambiguous_retry_from_create_refused_reenters_operator_path` |
| Legacy `create_intent` normal path stays ambiguous | `test_legacy_create_intent_normal_path_stays_ambiguous` |
| `recover_card_intent` adopts an (archived) card with zero creates | `test_recover_card_intent_adopts_archived_card_without_create` |
| `recover_card_intent` miss makes one create, `created`/`recovered_from` | `test_recover_card_intent_miss_creates_once`, `test_recover_card_intent_review_stage_after_created_implement` |
| `recover_card_intent` refusal gives `create_refused`/`recovered_from` | `test_recover_card_intent_refusal_records_create_refused` |
| `recover_card_intent` ambiguous exception leaves the row byte-identical | `test_recover_card_intent_ambiguous_leaves_entry_byte_identical` |
| `recover_card_intent` refuses unapproved, wrong state, existing ID, stale generation, missing predecessor without writing | `test_recover_card_intent_refuses_without_writing` |
| `refusals` history keeps the last 20 | `test_refusal_history_is_bounded` |
| Retiring a generation turns a no-ID `create_refused` into `refused_retired`; a no-ID `create_intent` stays ambiguous | `test_retired_generation_create_refused_is_absent_not_ambiguous`, `test_retired_generation_plain_create_intent_stays_ambiguous` |
| The retry from `create_refused` has saved `create_intent` (stored row) before its `create_card` | `test_retry_from_create_refused_persists_create_intent_before_create` |
| `recover_card_intent` holds the store lock (process lock and flock) across its `recover_card` and `create_card` | `test_recover_card_intent_holds_store_lock_across_recover_and_create` |

The service tests use the fake ports; the bridge tests use fake native callables.
A real core refusal round trip is untested here. The `refused_retired` row above uses a fake
`quiesce_generation`; the deployed retirer is covered by the integration tests named
in the `create_refused` section.

Before rollout independently review the source, then verify normal plugin discovery,
actual owner-session/tool attribution, project visibility, task-local sandboxing for
terminal/file/code execution separately, exact-head CI/review/delivery and real Slack
readback-before-archive while Desktop is unavailable. Leave deployment acceptance open
until those checks have real evidence.
