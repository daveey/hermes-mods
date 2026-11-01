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
transaction becomes visible. Replacement requests retain those exact card aliases;
an undecided card is rebound with revision CAS. Other Kanban references belong in `scope.sourceRefs`,
not in mutation-authorizing aliases. Failed publication can leave an inert blocked
card; it cannot expose an unbound decision or schedule execution.
The card carries a `human_request` (core refuses a blocked card without one,
`agent_owned_block`) and is typed `needs_input`, so the board shows it as waiting
on David: `action` = request title, `interaction` = `approval` (approval),
`reply` (choice/input) or `external` (external_step), `scope` = the scope's first
paragraph, `evidence` = `ActionRequest <id> revision <n> in the For you queue`.
Once David decides (approve, revise or decline), delivery commits the decision
comment + receipt and then archives the card, which clears its `human_request`;
card readback requires it closed, and a retried delivery re-attempts a failed
archive. Exception: a card other cards depend on (`task_links` children) stays
blocked, because archiving would release them and a decision never schedules
execution. A replacement revision after a decision gets a fresh card (same
creation key; core idempotency skips archived cards), appended as a new alias:
only the last kanban alias is live, earlier cards keep their decided binding.

## Persistence and API

Profile-scoped storage is under `<Hermes home>/user-steps/`:

- Existing lineage ledger JSON remains available for passive compatibility reads.
- `actions.sqlite3` stores canonical requests, revisions, aliases and durable
  decision operations before delivery side effects.
- `actions.sqlite3` runs in WAL mode (switched on every open, best effort), so lookups never wait on a
  committing writer; pure reads use deferred query-only snapshots and writes keep `BEGIN IMMEDIATE` with
  FULL sync. The per-reply hooks open it with a 5 s busy timeout (the store default stays 30 s), and in
  `transform_llm_output` a failed `reconcile` logs `user-steps reconcile failed; still recording this reply's
  asks` plus a `reconcile_error` trace line and still runs extraction (`tests/test_store_concurrency.py`).
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
so a decision always wins a race. A request bound to a decision card can be
withdrawn only by its agent (`action_request {"withdraw": ...}`): the undecided
card gets a "Withdrawn without a decision" comment and is archived, and repeating
the withdrawal retries a failed archive. The reconciler and the Desktop "Not
needed" button still never withdraw card-bound requests. The legacy ledger step behind it is marked
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

Retirement paths and evidence review:

- **deleted owner**: `reconcile_moot` withdraws an undecided request by `system` with
  reason `its chat no longer exists` after a successful read confirms the session is absent
  from its known local owner profile's `state.db`. Unknown profiles, foreign gateways,
  missing databases, read errors and locks keep the ask. Recorded decisions and bound
  decision cards remain untouched. Any existing chat's GET or hook also checks other
  undecided owners in the current local profile, since a deleted chat cannot read its own
  queue. Other profiles and gateways are not swept. `tests/test_owner_gone.py` covers
  these boundaries, the actual router path, and the hook path.
- **hygiene review**: `stale_watch.py` files one local Kanban review card per PT day for
  undecided, non-card-bound asks older than 72 hours that were not reviewed in the last
  seven days. The card includes request IDs/titles, owner-chat titles/last-turn times,
  and linked/named card states. Legacy revision-1 requests can use their matching ledger
  timestamp. Without a current-revision timestamp, a known local owner chat's last turn
  bounds the ask as "asked on or before" that turn; only bounds older than 72 hours
  qualify. Foreign/unknown/unreadable owners and chats without messages are skipped.
  `tests/test_old_ask_review.py` covers revision-2 missing lifecycle rows, recent and
  exact-72-hour bounds, owner isolation, labelled output and read-only selection.
  The worker withdraws only with evidence of terminal related work, a decision elsewhere
  (quoted), an expired window, a fixture, or agent-owned work it has done or filed.
  Otherwise it marks the request reviewed and keeps it. Use:
  `stale_watch.py --withdraw <requestId> --reason <text> --evidence <text>`
  (records `by='hygiene'`; refuses recorded decisions and card-bound requests), or
  `stale_watch.py --reviewed <requestId> --reason <text> --evidence <text>`.
  Run these as `python3 ~/.hermes/plugins/user-steps/stale_watch.py <arguments>` on the owning gateway.
  Before accessing any store the CLI checks identity and, if unavailable, re-execs once through the installed
  `hermes --print-runtime-command` launcher runtime, retaining its bootstrap/dependency selection and arguments.
  No install hash or dependency-generation path is pinned. If recovery fails, JSON status **503** (exit 2)
  names the interpreter/cause; neither mutation writes. Repair the runtime and retry rather than marking
  the ask kept. Only a known, different gateway gets **409** on withdrawal.
  `tests/test_stale_watch_runtime.py` exercises bare-interpreter re-exec, both mutation failure paths with
  unchanged stores, bounded recovery failures, detection preflight and genuine foreign-owner rejection.
  Review timestamps are revision-specific. `--dry-run` is read-only and cannot be combined
  with either mutation command. `tests/test_old_ask_review.py` covers selection, daily
  card creation, owner/card metadata, CLI refusal/withdrawal and retained-request reviews.

**Live fixture rule:** publish test requests only in scratch chats, then delete those chats
in `finally`. Deleted-owner reconciliation retires their undecided requests on the next
`GET /requests` or `pre_llm_call`. Never publish live fixtures in persistent chats.

Existing card/user triggers:

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
  is done or archived after the ask, or its own human ask was resolved outside Hermes,
  and every other named card is finished too. The latter requires the request to restate
  the pending `human_request.action`, a matching `resolved_check` command with integer
  exit code 0 in `ask_resolved`, followed by `unblocked {via: ask_resolved}`, with no
  intervening lifecycle change or later wait. It withdraws the canonical request and
  removes its extracted step from public `open_steps`, without approval or dispatch.
  Ordinary unblocking does not retire an ask: agents unblock to retry, and the retry often
  hits the same wall. An unknown, ambiguous or unreadable id, a named card still
  in flight, or a card that was running when asked ("check the screenshots when it
  finishes") keeps the ask. A stale tile is recoverable; a wrongly retired ask is lost.
  `tests/test_ask_resolved_retirement.py` exercises canonical ActionStore withdrawal,
  `GET /requests`, extracted `open_steps`, and legacy open-asks' status-based retirement,
  plus negative evidence, revisions, ownership, re-blocks and recorded decisions. Its
  board fixtures mirror core's event protocol; real probe production is tested in core's
  `test_kanban_ask_resolved_check.py`.
- **agent**: only the owning chat, explicitly:
  `action_request {"withdraw": {"requestId", "reason"}}`. The extractor's
  `resolved` list never withdraws a canonical ask (it retires legacy steps only, as
  before). In a turn a runtime row started (a Kanban notice or auto-continue,
  labeled `[Automatic notice, not typed by the user]` by `pre_llm_call`), it never
  retires a step the reply still restates (quotes its text, or the model lists it
  under `repeats`): a notice answers nothing. The per-turn context says how to withdraw, and reports each system/user
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

## One decision, one tile (by provenance, never by title)

Two open requests with the same title are two requests. Equal or similar titles,
shared card ids, overlapping words and matching menu labels are never identity
(implementation plan acceptance #2: no fuzzy dedup; missing identity does not trigger
fuzzy dedup). `same_open_ask` (core.py) frames an extracted ask onto an existing
open, undecided request of the same session only with exact provenance:

- the extractor listed that exact sentence under `repeats` for the open id (request id
  or extracted step id; the prompt lists producer-published requests as open steps),
  and the reply corroborates it (below);
- the evidence sentence (30+ characters, 5+ words) is the sentence an open ask was
  extracted from in an EARLIER reply;
- the request was published or revised by the `action_request` tool in this very turn
  (server-recorded `turn_requests`; reset when the next turn starts), and the ask
  restates its title or is the approve/change/decline menu of the only such approval;
- the same sentence of this reply was already read as an approval (one sentence, one
  approval).

Everything else is its own request: a same-title ask in a later reply that the model
did not mark as a repeat, a reworded ask naming the same card, a menu with the labels
of an approval from an earlier turn. Decided, withdrawn and closed requests are never a
target. The final-reply plan gate uses the same matcher, so a restated approval from
an earlier turn holds the reply and the nudge asks the agent to revise THAT request by
its requestId (which is turn provenance). `restates_open_ask` keeps the old heuristics
for the duplicate detectors only (`chat.py`, `stale_watch.py`); their cards tell the
agent to withdraw only when the source proves a re-ask.

`action_request`: a new request revises an open, undecided, card-free request of the
session in place only when it cites that request's own extracted step: an exact
qualified `user_step` ref (`gatewayId`, `profile`, `id` of this owner) in `aliases` or
`scope.sourceRefs`. Refs of another gateway, profile or session fold nothing. An exact
re-send of an open request (same kind, title, scope, limits, exclusions, outcomes,
choices, state: a lost response or retried call) returns that request unchanged with
a note. A same-title request is published separately and the `note` names the open
same-title requests so the agent can withdraw one it replaces; nothing is withdrawn
automatically. The in-place revision is refused if a decision was recorded meanwhile
(`revise(..., undecided=True)`), and the request is then published as new without the
decided request's refs: the decision wins. If the cited request was only revised
meanwhile (stale revision, still undecided), the call fails with 409 naming that
request, so one source step never gets a second tile. Citing steps of two open
requests at once is refused. A request that says, unscoped, that it asks nothing (title "No
decision needed: ...", scope "Settled, nothing to decide.") is never published: a
revision (with `expectedRevision`) withdraws the request, a new one is refused with
the withdraw instruction. None of this records a decision or grants anything.
`tests/test_one_open_ask.py` replays each live duplicate and the adversarial
review's keep-both cases.

**A model repeat needs corroboration.** The extractor named the session's only open
tile (the CuaDriver Accessibility step) as the "repeat" of four unrelated status
paragraphs, and each rendered as that tile with its buttons. `core.split_repeats` now
keeps a repeat only when the sentence or its paragraph backs it up
(`repeat_corroborated`): it quotes the tile's text or extracted sentence (a normalized
substring of 20+ characters), shares at least two content words with the tile's text,
sentence and title, names the same card id, or is a short topic-free reminder ("They'll
stay in your list until you answer them": no content word of its own). An
uncorroborated repeat is neither framed onto the tile nor mapped by `same_open_ask`,
`restates_open_ask` or `card_ask_for`; the other rules judge the sentence, and the
ledger's `repeat_checks` list records `{verdict: 'repeat_uncorroborated', tile, ...}`
(last 50 kept). `_still_asked` (which keeps a step open) still reads the raw repeats.
Tests: `tests/test_relay_corroboration.py`.

### A blocked Kanban card's own ask

A blocked card with a `human_request` already is the request in **For you**. A reply
that relays it (a chat-closeout turn told to pass the ask on, a Kanban blocked notice)
used to mint a second, unlinked tile with the card's choices; whichever one the user
answered, the other went stale. `core.kanban_asks` now lists, on every live local board
(`kanban/boards/_archived` skipped), the cards with status `blocked` and a
`human_request` that are linked to this chat's lineage (`kanban_notify_subs.chat_id`)
or named by id in this turn's runtime message (chat-closeout or Kanban notice; a card id
the user typed adds nothing). Each is an open ask
`{id: 'kanban:<board>:<task_id>', kind: 'kanban_ask', text: human_request.action, title}` for
both the extraction hook (`transform_llm_output` → `process_reply`) and the plan gate
(`publication_buffer`); the prompt shows it as `card <task_id> "<title>": ask: <action>`.
A step that `card_ask_for` matches to one (the model listed the sentence as a repeat of
that id and the reply corroborates it; the step contains the ask, or a specific sentence
of it; its menu offers two of the card's choice labels; 60%+ of its content words, at
least three, are the ask's words; it tells the user to unblock or archive a card ("unblock"/"archive"
plus "card" or a card id) and shares a distinctive word with exactly one listed card's
title, or names its id (generic words such as card, agent, window, request, macbook,
hermes, prompt, terminal and function words identify nothing); or half its words are
exactly one card's title plus ask words) publishes nothing. A compound step ("Unblock
the fseventsd card, then approve Touch ID in the terminal window") is covered when its
first action is: the follow-on is the card's own next step. Such a step is not framed,
does not hold the gate, and is recorded (a relay the model reports only under "repeats"
with the card's id is recorded the same way) in the session ledger's `card_asks` list as
`{verdict: 'restates_card_ask', card, board, ...}` (last 50 kept). Word overlap is
allowed here, unlike between requests, because the card's link to this chat (or the
notice naming it) is the provenance, and the match only suppresses: it never answers,
decides, edits or unblocks the card. Once the card leaves `blocked` (unblocked, done,
archived) its ask leaves the list and a later re-ask publishes as before; steps that
match no card behave as before, and an unreadable board lists nothing (fail open).
Tests: `tests/test_card_asks.py`, and `tests/test_relay_corroboration.py` for relays that
name the card by its title topic (the live fseventsd relay; two cards, one named; an
unknown topic still publishes) on both the extraction hook and the gate.

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

TUI-runtime decisions use `prompt.admission.get`/`prompt.admit` in the already-loaded
owning serve runtime. Gateway local-platform (`local-*`) decisions instead use the
published gateway runner's profile-scoped authority and durable automation FIFO;
`operationId` is the immutable producer request identity. Admission readback verifies
the stored text digest, owner and request identity, including after compression.
A committed receipt remains readable when the route later rotates; a fresh decision
cannot target an unrelated replacement conversation. Confirmation means durable
admission, not successful model completion. Neither path uses `/v1/runs`, port
discovery, session resume or a competing writer. Unconfirmed delivery remains visible
with Retry in For-you; classified backend warnings identify the operation without
logging raw transport details. Tests cover TUI admission, real local gateway FIFO
(including a compressed child), failed-row retention and terminal receipt outcomes.
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
patch 0723) hands the session's recorded-but-undelivered decisions to the clarify as
`{status: answered_elsewhere, decisions: [{requestId, title, choice, decidedAt, via}]}` before it asks and every 2 s
while it waits. "The session" is the owner's stored id or a compression ancestor/descendant of it. The operations are
then delivered with a receipt `run: {destination: clarify_early_answer, toolCallId}` (cards and inbox as usual, no
turn admitted), so the retrier never repeats them. A busy refusal is counted in `busyRefusals`, not `attempts`, so
waiting out a long turn cannot exhaust the retry cap. Delivery after a turn ends is still the 15 s retrier (no
turn-end hook). Tests: `tests/test_clarify_early.py`.

## Tests

Verify standalone CI separately from the core-enabled suite: local auto-discovery can
otherwise hide accidental core dependencies. From `plugins/user-steps`, run this
with both `--python 3.11` and `--python 3.14`:

```sh
HERMES_CORE_DIR=/nonexistent PYTHONPATH= uv run --no-project --python 3.14 --with pytest --with pyyaml --with fastapi --with httpx python -m pytest -q -p no:cacheprovider tests
```

Deleted-owner tests supply only the profile-discovery module seam; they still run
real SQLite reconciliation, GET `/requests`, and `pre_llm_call` without core installed.
The fixture restores its module entries and package attribute after each case.

From the repository root:

```sh
uv run --with pytest --with pyyaml --with fastapi --with httpx --with ruamel.yaml python -m pytest -q -p no:cacheprovider plugins/user-steps/tests -rs
HERMES_DESKTOP_DIR=/path/to/test-dependencies node --test --test-reporter=tap desktop-plugins/user-steps/decision-queue.test.mjs desktop-plugins/user-steps/decision-queue.browser.test.mjs desktop-plugins/user-steps/test/*.test.mjs desktop-plugins/session-tasks/test/*.test.mjs desktop-plugins/blocked-panel/test/*.test.mjs
```

`tests/conftest.py` puts the core checkout (`HERMES_CORE_DIR`, else `~/.hermes/hermes-agent`) on
`sys.path`, so the real-core Kanban tests (`test_decision_card_real_core.py`, `test_card_delivery_core.py`)
run instead of skipping. Inside a Kanban worker the delegated-write fence covers `~/.hermes`, so pass
`--basetemp` outside it (e.g. `--basetemp ~/.cache/us-pytest`) or those tests skip. The three
`tui_gateway` tests (`test_runtime_integration.py`, `test_dispatch_cancellation_core.py`,
`test_mounted_runtime.py`) skip with that instruction unless core's `tests.conftest` is the
active plugin; run them with `run-core-integration.sh <core worktree>`. `tests/core_runtime.py`
gates them before any core import: importing `tui_gateway.server` from a self-updating install
under a foreign interpreter (e.g. the legacy `~/.hermes/hermes-agent/.venv` python) lets core's
bootstrap re-exec the pytest process into the install runtime, which has no pytest, and the run
dies with exit 1 and no output.

`HERMES_DESKTOP_DIR` points to a package directory containing React, react-dom,
jsdom, esbuild and Playwright with installed Chromium. If omitted, the tests use
`~/.hermes/hermes-agent/apps/desktop`, matching existing Desktop test conventions.
No Playwright dependency is added to the runtime plugin.

The standalone runner omits core-only integration tests when core is unavailable.
The `user-steps` workflow runs that suite without core on Python 3.11 and 3.14.
`tests/test_core_compatibility.py` forces both early-answer import branches and
checks that missing/incompatible publication capabilities still refuse registration;
`test_registered_producer_revise_and_consumer_hook` supplies a supported-core fixture
without bypassing that capability check.
For the full backend suite, use an isolated core worktree with this feature:

```sh
git -C ~/.hermes/hermes-agent worktree add --detach ~/code/hermes-core-wt-usteps HEAD   # once
bash plugins/user-steps/run-core-integration.sh ~/code/hermes-core-wt-usteps
```

This copies source fixtures into the core test checkout, runs the canonical isolated
runner, and cleans those copies. It preserves the home-I/O and mutation guards. It refuses
the live install itself (`$HERMES_HOME/hermes-agent`) and forwards extra arguments to
`scripts/run_tests.sh` (e.g. `-k mounted`).
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
user reply is older than 1 h (the `/ping` `stale.answeredOpen` set), and one card per pair of open requests in
different sessions that `restates_open_ask` flags as possibly the same decision (key `user-steps:dup:<namespace>:<id>:<id>`).
Overlap is not identity: the card asks the agent to withdraw only when the source proves a relay.
Run it periodically on every gateway, since action
stores are per host: `python3 ~/.hermes/plugins/user-steps/stale_watch.py` (`--dry-run`, `--min-age SECONDS`).
Tests: `tests/test_stale_watch.py` (2 h reply -> one card; rerun -> no duplicate; retired -> none; CLI args;
cross-session pair -> one card; a single-session request -> none).

It also opens one card per delivered decision (key `user-steps-failed-decision:<operationId>`) whose admitted turn
ended in a failed-turn boundary before any tool ran and was not continued (no `auto_continue` row, no later user
message) within 10 min. The tile closes at admission, so a turn a backend restart killed otherwise loses the
decision silently. Core now continues such a turn after a restart (the marker stays auto-continuable until the
first tool starts); this card is the net for when that recovery misses. Tests: `tests/test_failed_decision_watch.py`
(failed without tools -> one card, rerun same key; flat receipt shape; continued / tools ran / recent / succeeded
-> none; decline / undelivered -> none).

A reply that only lists decisions waiting in other chats, queues or cards is a status report. A step whose
evidence sits in that paragraph is not minted, unless the span itself is a direct request and does not contain
the digest marker. In a turn a runtime notice started (a Kanban notice, owner wake or
chat-closeout row: relay provenance) the extractor receives up to 30 titles of open asks
in other sessions (`ELSEWHERE`); a sentence mapped to one of those titles is neither
minted nor framed. Before publishing extracted steps in such a turn, a deterministic read
rechecks **all** undecided other-session titles in the same gateway/profile
namespace, without the prompt's recency cap. Exact normalized candidate-title or
specific evidence-sentence equality suppresses that relay even when the extractor
omits its `elsewhere` mapping. In a turn the user started, the prompt lists no other
chats and an equal title in another chat is no source association: the ask is
published as this chat's own request. This
never adopts or frames another session's request or changes its state, revision or decisions.
Unrelated asks, other namespaces and retired requests remain independent. This is a
publication-time snapshot, not atomic concurrent-session admission or fuzzy matching.
Tests: `tests/test_elsewhere_digest.py` (missing mapping beyond 30 titles; a title
created during extraction; evidence equality; owner immutability/no operation;
namespace and retired-request isolation; unrelated asks).
