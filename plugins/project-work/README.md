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
  binds the plan hash; followups invalidate approvals, evidence and old delivery state.
- Native `session.create`, `prompt.submit`, `session.resume`, `session.archive` bindings
  and owner-conversation `kanban_create` tool calls, not parallel session/task stores.
- Implementation → independent review → release graph; closeout checks prior outstanding
  generations, exact-head tests/checks/review/delivery facts, writer and approval state.
- Original-thread Slack delivery, deterministic message key, paginated exact readback,
  latest-inbound check, then exact-session archival and a second check. A late change
  rolls the archive back. No session or transcript deletion is implemented.
- Restart reconciliation and exact-effect recovery. An unprovable prompt or send is
  retained as an error, never blindly retried. Failed work remains visible.

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
- `cards(id)` returns native `id`, `status`, `body`, `board`, and ordered `parents`.
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
workflow thread are required followups, including messages without a mention. Bots and
message subtypes are not accepted as new authority.

A trusted owner-conversation/runtime binding collects the concrete planning response,
then calls local `propose_plan(workflow_id, plan_text)`, posts the plan/hash in the source
thread and awaits the owner's exact approval command. Host callers use `Client.call`
with named arguments. After all graph stages finish, call
`closeout(workflow_id, result_text)`; the runtime durably owns retries after this point.
Missed task-completion triggers are reconciled from freshly verified native evidence:
the controller posts a deterministic receipt naming the repository and completed cards.
It does not invent a work summary from task prose. Planning proposals still require
the deployment's native planning-response hook/controller.

Local operations are `request(context, text)`, `status(context, workflow_id)`,
`approve(context, workflow_id, plan_hash)`, `observe_followup(context, text)`,
`propose_plan(workflow_id, plan_text)`, `closeout(workflow_id, result_text)`, and `reconcile()`.
The socket is a trusted host capability, not a remote API. Its filesystem boundary is
part of the deployment security model; all same-UID host code is trusted. Never expose
it through an unauthenticated proxy or model terminal.

## Failures and recovery

Inspect status `state` and `error`. `create_intent`, prompt `intent` and `post_intent`
are persisted BEFORE effects. Recovery requires proof of exactly the intended effect:
unique native title/ID, exact user prompt marker, or original-thread bot message with
matching deterministic `client_msg_id` and text. No proof means no replay and no archive.
Never manually change ledger status to `archived` or fabricate delivery receipts.

Archive reads exact native identity, source, profile, cwd and title; Slack-origin or
parented sessions are refused. Native compression/lineage changes that cannot satisfy
these exact checks require explicit deployment reconciliation, not fuzzy title fallback.
An unavailable runtime/reader is an error, not evidence of completion. Followups reopen
an archived owned session and require a new plan confirmation. The platform's own
home-channel configuration is neither read nor changed by the plugin.

## Tests

Run from the repository root:

```sh
uv run --with pytest python -m pytest -q plugins/project-work/tests
```

Coverage includes scope rejection, invalid paths/owner types, plan approval and stale
hashes, duplicate requests across threads/processes, crash recovery, generation gates,
missing/stale exact-head checks, uncertain send, late followups, native method/parameter
contracts, Slack pagination, exact-session archive rollback, strict private config,
native ingress parsing, and a real local Unix-socket controller/client round trip.
Native RPC/Slack tests use deterministic fakes and are NOT live end-to-end proof.

Before rollout independently review the source, then verify normal plugin discovery,
actual owner-session/tool attribution, project visibility, task-local sandboxing for
terminal/file/code execution separately, exact-head CI/review/delivery and real Slack
readback-before-archive while Desktop is unavailable. Leave deployment acceptance open
until those checks have real evidence.
