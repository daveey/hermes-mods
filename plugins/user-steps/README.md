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
answer, without approval/plan controls. Answers grant no execution permission,
with one exception: on a question about when or whether to run something, a go
option ("now", "when idle", "yes") is marked `authorizes: true` (shown as ✓) and
picking it records `executionAuthorized: true` for exactly the frozen scope on the
tile. "later", "wait", "no" and free text grant nothing (`tests/test_decisions.py`).
The question's own "A, or B?" alternatives are buttons; a model option found
verbatim is added when it is not already part of one. An "other" tail
("something else", "neither") is never a button: the free-text answer covers it
(a lone "something else" button once hid the real choice, desktop-1321064734).
Existing revision-1 legacy clarifications receive a mechanical question-only
revision, retaining their exact source and a server-owned migration receipt.

Incomplete approvals are agent work: the producer rejects them with a completion
instruction, and the buffered final gate nudges the agent to publish the exact
inline plan with approve/change/decline choices. At budget exhaustion it withholds
the approval ask. Existing incomplete cards say **The agent asked you to approve
this without a plan** and offer only **Ask agent for the plan** (drafts that ask
in the composer) and **Dismiss** (hides that revision locally; a revised request
shows again). No greyed-out Approve or "request changes" control. Buttons retain
44px targets and owner/revision safety. A complete published request is not
duplicated by prose extraction.

`GET /ping` also returns `stale`: `answeredOpen` (open clarifications whose framed
question already has a later user reply, i.e. chat retirement did not close them),
`planPending` (plan-less asks waiting on the agent) and up to 20 `answeredOpenIds`.
Read-only.

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
type, ID and board where applicable. Similar titles are never merged across sessions or into a decided,
withdrawn or closed request. Within one session a re-ask of an OPEN, undecided request reuses its tile
(see "One decision, one tile" below).

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

## Lifecycle: withdrawn asks

An ask that is no longer needed leaves the queue through `withdrawn`, a terminal
state that is **not a decision**: it grants nothing, admits no turn and delivers
nothing. It is refused (409) once a decision is recorded for the current revision,
so a decision always wins a race, and it is never available for a request bound to
a decision card (that card decides it). The legacy ledger step behind it is marked
resolved (`No longer needed: <reason>`, `withdrawn: true`), which keeps the passive
strip and ask-gate consistent. A withdrawal is not a decline: asking the same thing
again later is a fresh ask, never held as a repeat. Lifecycle data (ask time and
the revision it belongs to, the withdrawal record, notification state) lives in the
`lifecycle` side table, so older processes' positional `requests` inserts keep
working. `_get` keeps the exact frozen body (card bindings compare it byte for
byte); `view()` adds `createdAt` / `withdrawal` for REST and hook readers.

Ask time: a producer revision (and any revival of a withdrawn request) is a fresh
ask; a server-side re-render of a legacy row keeps the original time; a revision
written by a process that predates the stamp is treated as fresh. Each errs toward
keeping an ask.

Three triggers:

- **system**: `reconcile_moot` runs on every `GET /requests` and every
  `pre_llm_call`, in one deferred read with a 2 s busy timeout (no write lock
  unless something retires). It watches the Kanban ids in the ask itself (request
  title and the extracted step's text, plus explicit same-owner `kanban`
  `sourceRefs`; never the evidence sentence or surrounding markdown, which name
  context too) on live local boards (`kanban/boards/_*` skipped). A card counts
  only if it was **waiting** on someone when the ask was made: its last relevant
  event before the ask was `blocked`, `review_requested`, `block_loop_detected` or
  `gave_up` (a reviewer's claim keeps the review wait; a worker's claim, an unblock
  or a manual promote ends it). The ask is withdrawn only when every counted card
  is done or archived after the ask, and every other named card is finished too.
  Unblocking does not retire an ask: agents unblock to retry, and the retry often
  hits the same wall. An unknown, ambiguous or unreadable id, a named card still
  in flight, or a card that was running when asked ("check the screenshots when it
  finishes") keeps the ask. A stale tile is recoverable; a wrongly retired ask is lost.
- **agent**: only the owning chat, explicitly:
  `action_request {"withdraw": {"requestId", "reason"}}`. The extractor's
  `resolved` list never withdraws a canonical ask (it retires legacy steps only, as
  before). The per-turn context says how to withdraw, and reports each system/user
  withdrawal to the chat once.
- **user**: `POST /requests/{id}/withdraw` (Desktop "Not needed").

`GET /requests` omits withdrawn rows unless `include=withdrawn`, and so does
`ActionStore.requests()` by default: route bodies mounted before this state existed
call it, and renderers before v13 reject a whole queue that holds an unknown state.
`GET /ping` advertises `features: ["withdraw"]`.

Extraction precision: a reply whose sentence, in full, declares that nothing is
needed from the user ("Nothing is currently assigned to you.", "No action is
required.") cannot also carry an ask in a plain status line; in such a reply only
evidence that directly asks (question, please, can you, I need your..., an
imperative) survives, and quoted text (someone else's words) does not count. A
scoped declaration ("nothing is needed from you for the deploy", "t_A: nothing for
you") switches nothing off; "nothing else for you" never counts. This also keeps the
final-reply plan gate from holding such a reply. The extractor prompt says status
lines and Kanban review lanes are not asks.

`tests/test_withdrawal.py` covers the transition, refusal once decided, card-bound
refusal, revival, the tool and user paths, the extractor never withdrawing, re-asks
after a withdrawal, card done / retried after an unblock / triage / already done /
running when asked / reviewer vs worker claims / still in flight / named only in the
evidence / unknown / unreadable board / all-cards, millisecond event times, archived
boards, ask time across re-renders, producer revisions and older-process revisions,
the lock-free pass, `requests()` hiding withdrawn rows, REST filtering and dismissal,
hook context, the plan gate, and the extraction guard (scoped and unscoped
declarations, quoted questions, and the exact reply that produced a stale approval ask).

## One decision, one tile

The aux model rewords every restatement of an ask ("Decide on t_X: ..." becomes
"Decide whether to ... (t_X)"), so exact action identity alone minted a new request
and tile for every reply that mentioned an open ask (3-5 tiles per decision in live
chats). `same_open_ask` (core.py) maps a re-ask to the ONE open, undecided request
of the same session it repeats, and the reply is framed onto that tile instead:

- its text or evidence sentence IS an open request's title (markdown, list marks and
  end punctuation ignored), or the same evidence sentence an open ask was extracted
  from in an earlier reply;
- the model listed that sentence under `repeats` for an open id (the prompt now lists
  producer-published requests as open steps and says a restatement is a repeat);
- it names exactly one Kanban card, and an open request of the same kind names only
  that card;
- it is the approve/change/decline menu and exactly one open approval exists (or
  that sentence was already read as one approval).

Anything else stays a separate ask: a different kind on the same card, a menu next
to two open approvals, two asks read from one sentence of one reply. Decided,
withdrawn and closed requests are never a target, so asking again after a decision
or withdrawal is still a fresh ask. The final-reply plan gate uses the same matcher,
so a reworded restatement of a published approval no longer holds the reply.

Card and menu rules also need the same words: two different asks about one card
("register a runner for t_X" / "delete t_X's stale branch") stay separate, a card
named only in the evidence sentence anchors nothing, and a menu must share the
approval's labels and topic. Evidence-sentence rules need a specific sentence (30+
characters, 5+ words), so "Let me know which you prefer." merges nothing.

`action_request`: a new request whose title (20+ characters) matches an open,
undecided, card-free request of the session revises that request in place when it
is provably the same ask (an extracted or plan-less tile, or the same plan re-sent)
and withdraws older such duplicates (`by: agent`, `Duplicate of <id>`). A complete
plan with a different scope under the same title is published separately and the
result's `note` names the other open request to withdraw if it is replaced. The
in-place revision is refused if a decision was recorded meanwhile
(`revise(..., undecided=True)`), and the request is then published as new: the
decision wins. A request that says, unscoped, that it asks nothing (title "No
decision needed: ...", scope "Settled, nothing to decide.") is never published: a
revision (with `expectedRevision`) withdraws the request, a new one is refused with
the withdraw instruction. None of this records a decision or grants anything.
`tests/test_one_open_ask.py` replays each live duplicate and the adversarial
review's keep-both cases.

## Limits

Routes under `/api/plugins/user-steps`:

- `GET /ping`
- `GET /requests?session=...[&include=withdrawn]`
- `GET /requests/{requestId}?session=...`
- `POST /requests/{requestId}/decisions` with `session`, `expectedRevision`,
  `choiceId`, optional `revisionText`, and `idempotencyKey`
- `POST /requests/{requestId}/withdraw` with `session`, `expectedRevision` and an
  optional `reason`: the user removes an undecided ask; not a decision, nothing delivered.
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
Queue reads ask for withdrawn rows (`include=withdrawn`) so transcript receipts
read "No longer needed — <reason>" and their references stay suppressed, but
withdrawn requests are never shown or counted, and a retry intent left for one is
dropped (nothing can be pending on a withdrawn revision). Each undecided tile shows
when it was asked and, when the owner's `/ping` advertises `withdraw` and no
decision card is bound to it, a **Not needed** button that withdraws it on the owner
(one POST per tile; no decision recorded, nothing sent to the agent; a 409 refreshes
the queue). A plan-less ask offers **Ask agent for the plan** plus Not needed; an
owner without withdraw gets the local, per-revision **Dismiss** instead. Ping answers
are cached per route scope for 5 minutes; a failed ping is not cached.

Desktop disk code can hot-reload. Backend router/module activation must be verified
separately through the normal deployment route; no ad hoc restart is required by
this plugin documentation.

## Decisions for a session blocked in clarify

A decision is delivered as a new turn, which the runtime refuses while the session's turn is active. When that turn is
blocked in `clarify` on the same human, `clarify_early.py` (a provider for core's `tools/clarify_early.py`, core
patch 0722) hands the session's recorded-but-undelivered decisions to the clarify as
`{status: answered_elsewhere, decisions: [{requestId, title, choice, decidedAt, via}]}` before it asks and every 2 s
while it waits. "The session" is the owner's stored id or a compression ancestor/descendant of it. The operations are
then delivered with a receipt `run: {destination: clarify_early_answer, toolCallId}` (cards and inbox as usual, no
turn admitted), so the retrier never repeats them. A busy refusal is counted in `busyRefusals`, not `attempts`, so
waiting out a long turn cannot exhaust the retry cap. Delivery after a turn ends is still the 15 s retrier (no
turn-end hook). Tests: `tests/test_clarify_early.py`.

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

## Stale-request detector

`stale_watch.py` opens one Kanban card (idempotency key `user-steps-stale:<requestId>`) for each open clarification whose
user reply is older than 1 h (the `/ping` `stale.answeredOpen` set). Run it periodically on every gateway, since action
stores are per host: `python3 ~/.hermes/plugins/user-steps/stale_watch.py` (`--dry-run`, `--min-age SECONDS`).
Tests: `tests/test_stale_watch.py` (2 h reply -> one card; rerun -> no duplicate; retired -> none; CLI args).
