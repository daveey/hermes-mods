# user-steps

Canonical session action requests and one inline decision sheet above the Desktop
composer. This extends the existing user-steps ledger; it does not add a competing
queue. Transcript directives are passive receipts retaining their original text.
Agent progress remains separate and collapsed.

## Implementation status

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

Routes under `/api/plugins/user-steps`:

- `GET /ping`
- `GET /requests?session=...`
- `GET /requests/{requestId}?session=...`
- `POST /requests/{requestId}/decisions` with `session`, `expectedRevision`,
  `choiceId`, optional `revisionText`, and `idempotencyKey`
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
