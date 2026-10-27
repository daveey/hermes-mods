# user-steps

Canonical session action requests and one inline decision sheet above the Desktop
composer. This extends the existing user-steps ledger; it does not add a competing
queue. Transcript directives are passive receipts retaining their original text.
Agent progress remains separate and collapsed.

## Implementation status

## Legacy ledgers without `revision` (migration note)

Ledger files written before revisions existed have no `revision` key. They stay
editable: the first `Ledger.edit()` migrates them to `revision: 1`. Until that
edit, `read_exact()` deliberately raises `ValueError`, so lifecycle consumers
(ask-gate retirement) treat the step as unknown and fail closed instead of
trusting an unversioned snapshot. Test:
`tests/test_lifecycle_source.py::test_revision_new_migration_and_noop`.

## What you see (Hermes Desktop)

The decision sheet separates **Read this** (the inline plan rendered through the
Desktop Markdown component, limits and exclusions) from **Your decision** (the
existing choices and their exact outcomes). A single approval choice is labeled
**Approve** in green; revision is **Request changes**, and Decline is red. Custom
choice labels remain in the outcome description and button title; multiple
approval choices retain distinct labels. No choice IDs or authorization routes
change.

Clarifications preserve literal source choice labels and provide a free-text
answer, without approval/plan controls. Answers never grant execution permission.
Existing revision-1 legacy clarifications receive a mechanical question-only
revision, retaining their exact source and a server-owned migration receipt.

Incomplete approvals are agent work: the producer rejects them with a completion
instruction, and the buffered final gate nudges the agent to publish the exact
inline plan with approve/change/decline choices. At budget exhaustion it withholds
the approval ask. Existing incomplete cards say **Agent completion pending**; the
user does not need to request a plan. Buttons retain 44px targets and owner/revision
safety. A complete published request is not duplicated by prose extraction.

Chat reconciliation retires source-bound clarification questions only, using the
active same-session framed question and its first authentic user reply. Explicit
same-task redirection also needs a subsequent assistant completion. A constrained
auxiliary judge checks scope; quotes, source IDs, hashes and current revision are
rechecked before a suppression-only transaction. Ambiguous assent, foreign
sessions, arbitrary revisions, missing evidence and pending button decisions stay
unresolved. Approvals are NEVER approved or retired from chat by this path.
The receipt is separate from button operations: no synthetic approval, wake,
permission grant or card mutation occurs. Exact repeated retired questions are
held rather than re-published. Unframed or ambiguous evidence stays pending.

`tests/test_chat_clarifications.py` covers transcript redirection, migration,
answer outcomes, evidence races, owner/revision isolation, missing-plan behavior,
pending decisions, repeat suppression, and repair API payload rejection.

UI verification: `desktop-plugins/user-steps/decision-queue.test.mjs` covers
single dispatch across double clicks/two tiles, owner switches, stale revisions,
retries and delivery readbacks. `decision-queue.browser.test.mjs` bundles the real
Desktop Markdown renderer and tests formatting, real keyboard interaction,
agent-owned incomplete plans, clarification choices, 360px/640px widths and 200% zoom with sanitized fixtures.
Run from `desktop-plugins/user-steps` with Node 22+:

```sh
CHROME_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  /opt/homebrew/opt/nodejs/bin/node --test --test-reporter=tap \
  test/plugin.test.mjs decision-queue.test.mjs decision-queue.browser.test.mjs
```

`HERMES_DESKTOP_DIR` selects the core Desktop checkout and dependencies;
`CHROME_PATH` is optional if Playwright Chromium is installed.
`APPROVAL_SCREENSHOTS=<directory>` saves complete/incomplete 640px and 360px captures.
The browser test uses isolated fake REST, not live approval or execution.

This branch awaits independent review, exact-head CI, deployment and packaged
Desktop acceptance. Real-core isolated integration now exercises the REST-to-owner
admission path and card delivery without skipped tests. Cold, busy or rotated owners
still fail closed; durable admission is not proof that execution completed.

## Request contract

`action_request` creates or revises a schemaVersion 2 request on the owning runtime.
Identity is `(gatewayId, profile, requestId)`; gateway identity comes from the core
install identity, not a client-supplied owner or an invented environment variable.
Stored session association is exact. Aliases are qualified by gateway, profile,
type, ID and board where applicable. Similar titles are never deduplicated.

Requests carry immutable scope revisions, limits, exclusions, outcomes and explicit
semantic choices. Incomplete scope cannot be approved. Extraction and legacy
migration propose incomplete requests only; old prose never becomes authorization.
Optional permissions remain independent of scope approval.

Approve records the exact revision; Revise requires nonempty inline text and keeps
work blocked; Decline never wakes execution. Legacy generic Done/resolve controls
are disabled. Unknown cards remain passive references rather than gaining approval
buttons.

The producer can request `decisionCard: {"board": "default"}` alongside `request`
to create a dedicated blocked metadata card. It does not accept an existing card
ID. Core creation preserves its mutation fence; the exact owner/session, creation
key, immutable request snapshot and card fingerprint are bound before the request
transaction becomes visible. Replacement requests retain those exact card aliases
and rebind with revision CAS. Other Kanban references belong in `scope.sourceRefs`,
not in mutation-authorizing aliases. Failed publication can leave an inert blocked
card; it cannot expose an unbound decision or schedule execution.

## Persistence and API

Profile-scoped storage is under `<Hermes home>/user-steps/`:

- Existing lineage ledger JSON remains available for passive compatibility reads.
- `actions.sqlite3` stores canonical requests, revisions, aliases and durable
  decision operations before delivery side effects.
- Delivery records retain frozen payloads and destination receipts. A passive inbox
  write is not a successful execution handoff.

## Buffered publication (core-first rollout)

A no-op `pre_final_gate` callback declares `needs_buffer=True`, independently of
ask-gate. It neither judges nor holds a reply: it keeps final drafts private until
the existing output transform reconciles the ledger. Tool-call commentary retains
core's existing release behavior. Buffered verification interims stay private.
Registration requires core to advertise literal `True` for `needs_buffer` and
`post_transform_release` in `agent.pre_final_gate.PRE_FINAL_GATE_CAPABILITIES`.
Unsupported core refuses this plugin before hooks are installed; update core first.
This refusal is not a claim of active protection on an older runtime.

Buffering is static for foreground turns while the plugin is loaded, including
when the extraction off switch is set; disable the plugin itself to remove its
buffering contribution. Core still excludes its normal background contexts.

`tests/integration_f3/test_user_steps_only.py` exercises real normal discovery,
AIAgent streaming, verification continuation, source retirement, and SQLite
publication with only user-steps enabled (plain and framed replies). See
`tests/integration_f3/README.md` for the explicit checkout command. Compatibility
integration also checks refusal and registration rollback for both plugins.

## Lifecycle source contract

Every successful `Ledger.edit` advances a writer-owned integer `revision` under
its existing cross-process lock. Legacy records without a revision migrate on
the next edit. Corrupt evidence, invalid revisions, and root mismatches fail
rather than being silently reset. Atomic replacement follows a file fsync;
directory-sync failures after replacement are logged as durability errors, not
reported as aborted edits (the new state is already visible).

`Ledger.read_exact(root, step_id)` reads only that root's regular file, limited
to 1 MiB, and returns `{root, revision, step}`. It rejects missing, corrupt,
oversized, unrevisioned, symlinked, mismatched-root, or duplicate-target evidence;
it never searches other sessions. IDs are opaque exact source keys; directive
consumers must validate their own syntax and establish the compression root
from the current session, not treat the directive's session attribute as a root.
The snapshot limit does not restrict legacy writes or prevent resolving steps.

This is a source API, not a deployed lifecycle adapter. A resolved snapshot is
only conservative suppression evidence. Neither `resolved_by` nor a model/REST
resolution string proves a trusted human transition, grants execution permission,
or authorizes reopening. Trusted reopen integration remains separate work.

`process_reply` now reconciles extracted actions inside the source edit lock,
using exact `(kind, text, verbatim_span, ordered options)` identity in that root.
Open repeats reuse their ID; a resolved exact match holds the entire draft before
new steps, framing or card creation. Whole-draft holding deliberately sacrifices
unrelated narrative/actions because the evidence span cannot delimit dependent
approval instructions in other paragraphs. A positive retirement finding survives
later write/card-sync exceptions; failures before a finding retain the existing
fail-open behavior. Model fields cannot reopen a step. This is not paraphrase or
account matching: changed extraction fields and omitted extractions remain gaps.
Publication after lock release is not atomic with a later source resolution.

`tests/test_action_continuity.py` covers exact reuse/retirement, source lock races,
compression/profile isolation, budgets, regex fallback, forged reopen fields,
whole-draft holds and actual transform-wrapper write/card failures. These are
isolated tests, not live deployment or full final-gate ordering proof.

## Limits

Routes under `/api/plugins/user-steps`:

- `GET /ping`
- `GET /requests?session=...`
- `GET /requests/{requestId}?session=...`
- `POST /requests/{requestId}/decisions` with `session`, `expectedRevision`,
  `choiceId`, optional `revisionText`, and `idempotencyKey`
- `POST /requests/{requestId}/reconcile-chat` with only `session` and
  `expectedRevision`: verifies stored evidence; accepts no caller-supplied answer
  or desired result. New router mounts expose this route; existing mounts can use
  the same `chat.reconcile` lifecycle API without restarting active sessions.
- `GET /operations/{operationId}?session=...`
- `GET /operations?session=...&idempotencyKey=...`
- `POST /operations/{operationId}/retry?session=...` retries the frozen operation,
  including interrupted `pending` attempts, without accepting a replacement payload.
- `POST /reconcile?session=...` retries undelivered operations for that exact owner.
- Legacy `/steps` and `/step` are passive reads; `POST /resolve` returns 410.

Stale scope is a 409; incomplete approval is a 422. A recorded decision is not
success until destination readback confirms delivery. Dispatch states are
`pending`, `failed`, `delivered`, and terminal `cancelled`. Cancelled means core
proved the scheduler did not start and retired the approval user row from active
history; it is **not successful delivery**. No card approval or passive inbox
approval is published. Retry/reconciliation never schedules that operation again.
The sheet states that no permission was delivered and directs the user to ask for
a replacement revision; the producer may publish that revision, requiring a fresh
decision. Unknown execution and legacy failed receipts are not cancellation proof.
Retry retains the original idempotency key;
competing decisions cannot replace a recorded operation. Slow destination I/O does
not hold the global request-store write transaction.

Each attempt records `dispatchStartedAt`. Pending attempts are immediately safe to
retry with the same operation identity; no timeout is required for correctness.
Paste-control markers or text the core sanitizer would change are rejected with
422 before recording a decision, including markers in the frozen scope. Text is
never silently rewritten after the user reviews it.

Production uses `prompt.admission.get`/`prompt.admit` in the already-loaded owning
serve runtime, never `/v1/runs`, port discovery, session resume or a competing writer.
Card bindings are checked read-only first; approve/revise admission and readback
then precede card decision comments. Decline never admits a turn. Card comments,
binding decisions and receipts commit together through core's fenced transaction;
all effects preserve blocked task lifecycle, claims and dependencies. Admission
can succeed before a later card failure: retry recovers that receipt without a
second turn. Completion is reported only after every destination reads back.

The router lifespan reconciles persisted operations at startup, checking each
owner against the mounted runtime identity. Cold/busy/rotated owners remain
unconfirmed; manual recovery can run after the exact owner becomes available.
Hot-mounted routers and isolated plugin hosts may not run startup lifespan, so
the explicit recovery endpoints remain necessary. Neither reads nor `pre_llm_call`
start recovery. The hook omits recorded decisions from its unanswered-request list
so an admitted approval is not contradicted before the composite commit.
Live packaged Desktop/end-to-end runtime acceptance remains outstanding.

## Desktop behavior

`desktop-plugins/user-steps/plugin.js` provides the canonical queue in
`COMPOSER_AREAS.top`. Scope, limits and outcomes stay inline; Revise, pending,
failure/retry and confirmed states share the sheet. A local filter handles many
requests. Qualified aliases suppress matching blocked-panel controls and task
chips without conflating unrelated cards. Late responses are guarded by owner and
session. Failed backend reads preserve text and do not enable authorization.

Desktop disk code can hot-reload. Backend router/module activation must be verified
separately through the normal deployment route; no ad hoc restart is required by
this plugin documentation.

## Tests

From the repository root:

```sh
uv run --with pytest --with fastapi --with httpx pytest -q plugins/user-steps/tests
HERMES_DESKTOP_DIR=/path/to/test-dependencies node --test --test-reporter=tap desktop-plugins/user-steps/decision-queue.test.mjs desktop-plugins/user-steps/decision-queue.browser.test.mjs desktop-plugins/user-steps/test/*.test.mjs desktop-plugins/session-tasks/test/*.test.mjs desktop-plugins/blocked-panel/test/*.test.mjs
```

`HERMES_DESKTOP_DIR` points to a package directory containing React, react-dom,
jsdom, esbuild and Playwright with installed Chromium. If omitted, the tests use
`~/.hermes/hermes-agent/apps/desktop`, matching existing Desktop test conventions.
No Playwright dependency is added to the runtime plugin.

The standalone runner omits core-only integration tests when core is unavailable.
For the full backend suite, use an isolated core worktree with this feature:

```sh
bash plugins/user-steps/run-core-integration.sh /path/to/isolated/core/worktree
```

This copies source fixtures into the core test checkout, runs the canonical isolated
runner, and cleans those copies. It preserves the home-I/O and mutation guards.
Verified: 127 backend tests passed without skips, including actual FastAPI routing,
runtime identity, RPC admission and durable user-row readback (model execution alone
is suppressed), plus real-core card transactions. 115 DOM/browser/plugin tests
passed. Coverage includes
schema/migration, owner isolation, revision conflicts, durable operations, retry
payload identity, partial delivery failure, passive receipts, keyboard revision,
640px layout and long content at 200% zoom. These are isolated tests, not packaged
Desktop acceptance. Legacy live scripts exercise the old resolve flow and must not
be used as v2 acceptance or against an existing real approval request.

`test_review_regressions.py` exercises admission-time hook context, sanitizer
rejection without frozen decisions, admission-before-card ordering, partial
delivery recovery, actual FastAPI router startup/manual reconciliation, owner
isolation, and producer creation/binding/revision/publication rollback against
isolated SQLite adapters. Real-core card tests preserve delegated mutation fences;
skips are not live mutation or end-to-end verification.
