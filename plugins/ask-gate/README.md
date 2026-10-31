# ask-gate

Makes it mechanically hard for the agent to ask you for something without checking first.

Before a reply that asks you to do, decide or provide something reaches you, a cheap judge model reads the ask next to
what the agent actually did this turn, the live state of the cards involved, what you already said, and what the agent
can do for itself. A stale, repeated, unchecked or avoidable ask is held and the agent is nudged privately to verify or
do the work. You never see the held draft.

## Why (five whys)

One chat (a Gmail political-spam cleanup) produced four wrong asks in a row:

- **I1** "Needs you: the Cloudflare token lacks Email Routing, add it" + a blocked card. A token-broker scope could mint
  that permission itself; the card did exactly that the moment it was told to.
- **I2** "Needs you: revoke Superhuman" + a card titled "(needs David signed in)". It had been revoked long before;
  nobody checked.
- **I3** "Still waiting on you: t_…" about a card that had completed minutes earlier (the completion notice reached the
  chat late; the reply came from memory).
- **I4** "Only open item: cancel the subscription. Want me to…?" after the user had said it was handled; a worker
  handoff's "David's call" leftover was relayed as a question.

1. Why did he get four wrong asks? The agent asked before checking the premise, and answered from memory instead of live state.
2. Why? Every check lives in prose: a skill ("discover credentials, mint scoped tokens"), a skill ("never copy an unchecked premise into a card"), a memory note.
3. Why didn't the prose apply? The model has to choose to load a skill and obey it; neither skill was loaded, and under load rules get skipped.
4. Why was nothing else in the way? Nothing in the runtime sits between "the model wrote an ask" and "the user sees it", or between "the model creates a card that waits on the user" and "the card exists".
5. Root: correctness of an ask depended on the model's discipline, not on a mechanism. Fix: put the check in the runtime,
   where it runs whether or not the model remembers (this plugin), and give it the facts the model lacks (live state, ledger, capabilities).

## What it does

| Layer | Hook | Behavior |
|---|---|---|
| L1 | `pre_llm_call` | Injects a short block each turn: status of every card whose `tasks.session_id` is this chat (read straight from `kanban.db`, all boards) plus asks the user already answered (ledger). One sqlite read; silent when there is nothing to say. |
| L2 | `pre_final_gate` | If the final reply contains an ask (`core.find_asks`: "needs you", "still waiting on you", "your call", "want me to…?", "can you…", "if you'd like me to…", "up to you", a "Needs you:" heading with its list), the judge returns a verdict per ask: `ok`, `already_done`, `already_answered`, `unverified`, `agent_can_do`. Anything but `ok` holds the reply with a private nudge naming the evidence. Two nudges max, then the reply is delivered with a short "could not verify" note. Judge down or garbled → deliver, log, never break a chat. |
| L3 | `pre_tool_call` | Explicit `human_request` on `kanban_create`/`kanban_block` is validated and judged as a complete structured request (see below), never via unrelated body/reason text. Status or operational failure alone is not an ask. Legacy prose explicitly saying the user must act retains its `Checked:` gate; worker `kanban_complete` handoffs retain the existing verified-ask/FYI rewrite. |
| L4 | ledger | `~/.hermes/state/ask-ledger.sqlite` (not synced). When the user's message answers or dismisses an open ask ("done", "i keep completing it", "stop asking", "you can do it") the aux model classifies it and the topic/answer is stored. L1 shows it in every chat (cross-session); L2's judge treats a repeat as `already_answered`. A cheap regex gates the classifier call, so unrelated messages cost nothing. |

**Repeat asks (L2, no model call).** The judge passes a true, unanswered ask as `ok` every time, so nothing stopped a
chat from restating the same optional ask reply after reply. Every ask item a delivered reply shows (list items under
a heading count one by one) is recorded in the ledger's `shown_asks` table (session, topic tokens, text, the fact
tokens of its paragraph). Before the judge, an item whose topic this chat (compression lineage) already showed in
2 delivered replies within 48 h, the first at least 1 h ago, is held with: *Asked N times, last at T, still in
For-you or on its card. Don't restate it. If nothing waits on it, say once that silence means no, then close the
card.* Not held: an item that brings a new fact (a date, time, amount, deadline/expiry/cost/charge/failure word not
in the earlier showings), an item the user's last message is about, a how-to answer, repeats minutes apart while the
user works a step live. Topic match (`core.same_topic`) is conservative: a shared card id, or >=2 shared content
tokens covering >=60% of the shorter ask, never when both name identifiers that share none (PR 10 vs PR 12). Logs
`ask_repeat_held` (or `ask_repeat_released` once the two-nudge budget is spent: the reply then goes to the judge as
before). Held drafts are not counted as shown. Why: self-heal agent-31a66d274a (four replies over 43 h asked David to
revoke a leaked key and decide on secret hiding; nothing waited on either). Tests: `tests/test_repeat_ask.py`.
Replay (`scripts/replay_repeat.py`, coach digests, MacBook + hub, last 30 days, 2026-10-30): 3,884 replies, 838
with asks, 9 holds (0.23%); hand-checked all 9: 7 real restatements (incl. the incident reply), 1 duplicate digest,
1 false hold (a live phone-pairing step re-asked over 1 h).

**Worker handoff leftovers (L3).** `core.find_leftovers` reads a worker's `kanban_complete` summary for sentences
that hand David a step: they name him ("David's call", "needs you"), or they don't but still tell the reader to act
("please run/open/…", "need an unblock from your side", "run this … from your side / in your terminal"). Each
leftover the judge does not pass as a checked `ok` is rewritten `FYI (unverified, not an ask for David): …`. In a
`FINAL SEND for @session:<ref>:` line the mark goes after the header, because core relays the line only while it
starts with it. "Nothing … from your side/end" is status, not a leftover. Why: on 10-30 a hub worker's handoff told
David "Please run this from your side:" above an `ssh … hermes kanban unblock` loop, and no shape matched.
Tests: `tests/test_ask_gate.py` (`week_1026`, `shell_command_handback`, `final_send_leftover_keeps_its_header`).

**Chat-delivery asks (L3, no model call).** `chat_delivery.py` refuses a `kanban_block`, blocked `kanban_create` or
worker `kanban_complete` leftover whose ask is for a human to type, send or relay a message into a Hermes chat: a
delivery verb (send, type, ask, tell, relay, resume…) aimed at an `@session:` ref, a `session <id>` or "the <name>
chat", or a structured ask naming a chat this turn's `cross_session` send was refused for. The refusal names the
exact `FINAL SEND for @session:<ref>: <text>` line (a worker's refused send subscribes the chat to its card) and
non-worker `cross_session` as the route. Not matched: "reply in this chat", "tell me", physical/external
`human_request`s, sentences that already name `cross_session`/`FINAL SEND`. Runs before the judge and fails open; a
refusal logs `chat_delivery_ask_refused`. Why: self-heal agent-c25865e208 (workers asked David for hours to type a
line into a repair chat that a FINAL SEND delivered).

**Terminal hand-offs (L2, no model call).** Before the judge, `handoff.py` looks for a sentence that hands the user a
step a shell could do: an imperative (run / paste / type / enter / add / install…, after a sentence start, a comma,
"please", "you need to", "I'd rather you") aimed at something only a shell does (`sudo`, `ssh`, `authorized_keys`,
a key `.pub`, "terminal", `launchctl`, a `hermes update`…), or a "run this:" line followed by a shell fence, or a
"For you / still open" heading over a `sudo`/`ssh` fence. Unless this turn already put a terminal in front of the user
(`run_in_user_terminal`, or an osascript/`open -a Terminal` window), the reply is held with: *Do it yourself: call
run_in_user_terminal; the user types any password there. Hand back only truly physical steps.* Two nudges, then it
goes to the normal judge. Skipped when the user asked how to do something. Not matched: physical steps (taps, GUI and
web sign-ins, System Settings, hardware), codes pasted into the chat, approvals ("want me to run …?"), past-tense
reports, reversal how-tos ("To take it down, run …") and quoted speech. user-steps loads the same file to drop such
steps. Replay on real replies (private set): 19/19 hand-offs caught, 0/73 false alarms; 2 of ~2,000 recent replies
match, both in turns that had opened a terminal (exempt).

Deterministic first: an ask naming a card that is `done`/`archived` in the database is `already_done` without any model call.

The judge sees: the ask sentences, this turn's tool calls and results, the last 30 user messages, live status of every
card id mentioned, the ledger, and a **capability sheet generated at runtime** (token-broker scope names and tiers —
no values —, ssh hosts from `~/.ssh/config`, signed-in Chrome profile emails from Chrome's Local State, browser vault
entry names). The sheet is built in memory and never written to disk or committed. Ask validation refreshes
it immediately rather than trusting the optional 10-minute cache. Broker discovery runs only `scopes`,
tries the current `~/.local/bin/token-broker` before a distinct PATH client, and falls back on execution or
invalid-response failures. A successful list (including an empty list) is authoritative; malformed rows
are omitted, not interpreted as capabilities. Only validated scope names and known tiers reach the judge.
Scope discovery is evidence of an available credential route, not proof of vendor permission or authorization
to execute an action. Tests in `tests/test_f3_broker.py` exercise discovery through temporary executables and
the final-response gate with a stub judge; they do not vend credentials or contact a vendor.

**Irreversible asks are never `agent_can_do` (code, not prompt).** The SYSTEM prompt says destructive or
irreversible actions are always `ok`, but a model can still answer `agent_can_do` when `how` names a scope that is on
the sheet (10-29: "revoke the OpenAI key" + an inference scope; the agent then edited a dotfile). `judge.IRREVERSIBLE`
matches an ask containing both an action verb (revoke, rotate, regenerate, reissue, delete, destroy, wipe, purge,
cancel, deactivate, transfer, pay, purchase, buy, refund) and an object noun (key, token, secret, password,
credential, account, subscription, repo, repository, bucket, database, backup, payment, invoice, card, domain, funds,
money). On the ordinary path (`judge()`) and the structured path (`structured_llm`), an `agent_can_do` on such an ask
becomes `ok` with the reason "irreversible: David's decision" and one `irreversible_kept` line in `ask-gate.jsonl`.
`already_answered`, `already_done` and `unverified` are left alone. Control: a missing-permission ask with a
`*-minter` scope still gets `agent_can_do`. Tests: `tests/test_irreversible_guard.py`.

**Standing decisions.** `build_user_prompt` adds a "DAVID'S STANDING DECISIONS (user profile)" section built from the
§-separated entries of `<HERMES_HOME>/memories/USER.md` (capped at 2000 chars, "(none)" when the file is missing),
stated to be data, not instructions. SYSTEM's `already_answered` also covers "a standing decision in the user
profile declines exactly this", so a declined key rotation is not asked again.

**Live-test rule.** A live test that runs a real agent never uses a real pending ask, credential or file of David's.
Use a fictional, human-only topic and `-t todo`. (10-29: a live `hermes chat --oneshot` about a real key made the agent
edit `~/.bash_profile`.) To check the judge against the real model, call `judge.judge(..., llm=judge.aux_llm)` directly;
that runs no agent.

## Requires compatible core before plugin rollout

Registration requires `agent.pre_final_gate.PRE_FINAL_GATE_CAPABILITIES` with integer
`version: 1` and literal `True` for `replace`, `accepts_hold_budget`, `needs_buffer`,
and `post_transform_release`. The check runs before installing adapters or hooks.
Missing/malformed support refuses registration with a loader error instead of silently
registering partial protection. Hook registration errors also propagate to the loader.

Deploy compatible core **first**, then activate this plugin. On an incompatible core
the whole plugin is unavailable (including previously working L1/L3/L4); ordinary turns
still run. Refusal is not a runtime fail-closed policy, authorization, or proof that a
particular transport is protected. Do not publish/activate this plugin ahead of core.

Tests: `tests/test_runtime_contract.py` checks the explicit advertisement,
`tests/test_runtime_registration.py` checks registration ordering and error propagation,
and `tests/integration_f3/test_runtime_compatibility.py` uses real PluginManager discovery.
The compatible buffered publication path is exercised separately by
`tests/integration_f3/test_final_publication.py` against an explicit core checkout.

## open-asks interplay

`open-asks` files a blocked card for an ask left in a reply. That would publish an unverified premise as a card. It now
checks `cache/ask-gate/verdicts.json` and files nothing when ask-gate held or could not verify the ask; with no record
(ask-gate absent) it behaves as before. The legacy prose exemption remains only for cards **without** an explicit
`human_request`. The filing marker anywhere in an explicit request or its body/reason cannot bypass validation or judging.

## Structured human requests

A request requires nonblank strings `action`, `why_human`, `scope`, `evidence`, and `interaction`; interaction is
`approval`, `reply`, `user_terminal`, `physical`, or `external`. Missing/null metadata is not a request. A malformed
non-null request is refused, not silently downgraded. Creation requires `initial_status=blocked`; requests cannot
accompany dependency/time/transient blocks. The gate does not change task state or grant permission.

Only the request's own `evidence` (optionally prefixed `Checked:`) can satisfy the same-turn tool-evidence check.
Unrelated Checked lines in body/reason cannot supply it. The complete five-field object is then one judge item,
without prose detection or sentence truncation. A tool-name match is only a preliminary check: the judge must
verify that the output supports the actual action, scope and need for a human. Explicit requests fail closed on
malformed/missing verdicts or non-ok verdicts. A judge OUTAGE (ModelRoutingError, timeout, provider error: no
verdict was produced) is not a refusal: the gate retries on routing classes `fast`, then `standard`, then `deep`
(each only with an eligible subscription route, skipping the class the configured judge uses), and when none is
reachable it accepts the ask, which already passed the deterministic checks (schema, evidence matching a tool call
this turn, reconciliation, retirement), and logs `judge_unavailable`. Outages and internal gate errors never get
the "keep operational failures parked without a human_request" advice, which turned real asks into hidden waits.
Ordinary final replies and legacy prose keep their
existing failure behavior; this is not a replacement for core authorization/security enforcement.

An initial blocked status, capability label, or `sudo -n` authentication failure alone is an operational issue,
not evidence that the user owes an approval. Keep it parked and owned without inventing a human request. Standing
authorization does not authenticate the OS. For a verified authentication step, use the supported user-terminal
flow with the precise command/host and step; never request passwords or secret codes/tokens in chat. Metadata
neither authorizes execution, bypasses security denials, completes work nor automatically unblocks a card.

`tests/test_human_request.py` exercises create/block hooks, required fields, supported interactions, unrelated
prose/evidence, operational failures, open-asks marker smuggling, malformed verdicts, and ordinary clarification
preservation; `tests/test_judge_unavailable.py` covers outages (class fallback, `judge_unavailable` acceptance,
deterministic checks still enforced, real refusals unchanged, no park advice on gate errors). Security prompt delivery and refusal handling are tested with a stub judge; real-model semantic
accuracy and live UI/REST/CLI paths are not established by these plugin unit tests.

### PR-only approval wording and one-shot filing hold

`pr_approval_request.py` is a stdlib-only shared parser; it performs no I/O and has no owner allowlist:

- `request_target(row) -> tuple[str, int, str | None] | None`: strict PR-only action recognition.
  `row['human_request']` is a dict or JSON object string, with `interaction` of `approval`, `reply`, or
  `external`. References are GitHub PR URLs, `OWNER/REPO#N`, or `PR #N`. Short references resolve only
  through explicit matching PR references or `Repo:`/`Repository` labels in `title`, `body`, or request
  `scope`; ambiguous repositories and conflicting repository labels are rejected. A head comes only
  from the action, never from context. Private consumers must still enforce their owner policy and
  verify authoritative current-head approval receipts; parsing never grants permission.
- `scope_label(row) -> str | None`: extracts a syntactically valid optional scope label verbatim.
- `scope_label_failure(row, head_ref_name) -> str`: returns a diagnostic naming a label that does
  not exactly equal the authoritative PR `headRefName`, including absent branch metadata. Consumers
  must call it after fetching the PR and before reading approval receipts. Matching is case-sensitive;
  no label requires no branch metadata. Filing is syntax-only and does not perform this network check.
- `looks_like_pr_approval(row) -> bool`: broader diagnostic for leading Approve/OK/Authorize PR asks,
  not a strict parser or evidence of authorization.
- `parse_failure_reason(row) -> str`: empty on strict success; otherwise describes the unsupported
  wording and any missing/ambiguous correction metadata.
- `canonical_wording(row) -> str`: correction text with unambiguous supplied repository, number and
  head, or explicit `OWNER/REPO`, `N`, `SHA` placeholders. Correction hints may come from context even
  for rejected actions; they must **not** be used as approval targets. Lifecycle outcomes remain
  ellipses, not invented execution instructions.

The strict action accepts optional `please`, optional `at head/sha/commit SHA`, bare `head/sha/commit SHA`,
  or `(head/sha/commit SHA)` (7–40 hexadecimal characters), optional `on GitHub`, and optional
  `, or OK the bb approval ask in …`. Allowed surfaces are only Discord, Slack, BB Inbox, the BB Inbox,
  Inbox, or the Inbox, joined by `/`, commas, or `or`. The existing `Unblock = …; archive = …` suffix
  is recognized only when every full outcome clause is allowlisted (case-insensitive):
  Unblock permits queue/merge/merge train, deploy/release/ship, verify/check/re-run/watch CI,
  and continue/resume the task. Archive permits close the PR, drop/abandon the change,
  findings/card stay open, and leave it unmerged. Clauses combine with `and`, `,`, `+`, or `then`.
  Fixed compatibility phrases `agent merges via normal gates` and `PR left open` are also accepted.
  Arbitrary objects or trailing instructions are rejected, not screened against a verb denylist.
  Diagnostics quote the first unrecognised clause and its outcome side; malformed sections get
  a syntax diagnostic. Replace the canonical template's ellipses with concrete supported clauses.

The optional `scope` field is also fullmatched, never treated as free-form instructions:
  one PR URL, `OWNER/REPO#N`, `OWNER/REPO PR #N`, `PR #N`, bare `OWNER/REPO`, or
  legacy `Repo:`/`Repository OWNER/REPO`. It may have one parenthetical slug label (1–64
  ASCII alphanumeric/`.`/`_`/`-` characters, starting alphanumeric), one optional head/sha/commit
  plus 7–40 hex digits separated by spaces (optional comma), and trailing `only`.
  Only ASCII spaces separate scope tokens; extra prose, repeated fields, URL tails and other
  punctuation fail closed. Missing/empty scope remains supported for standalone parser callers;
  structured filing still requires nonblank scope. Scope heads remain correction hints only.
  Diagnostics quote the unrecognised scope text.

For structured `kanban_block` and `kanban_create(initial_status='blocked')`, an approval-looking request
that cannot strictly parse receives one deterministic wording hold per process-local session/tool/action+scope.
The message offers:

> Approve https://github.com/OWNER/REPO/pull/N (head SHA) on GitHub, or OK the bb approval ask in Discord, Slack or the BB Inbox. Unblock = …; archive = ….

An unchanged retry passes **this wording check**, including across turns; existing schema, stopped-workflow,
reconciliation, retirement, evidence and judge checks still apply. Canonical wording proceeds without the
new hold. This lint makes no model call, changes no task state, and neither approves nor unblocks anything.
It does not rewrite tool arguments or reset on each turn; plugin/process reload resets the one-shot memory.

Tests: `tests/test_pr_approval_request.py` covers syntax, surface variants, mixed-authorization rejection,
context ambiguity, JSON requests, owner independence and correction placeholders. `tests/test_pr_approval_filing.py`
registers the actual `pre_tool_call` entry for both create and block, exercises the hold/retry, canonical and
slash-separated regression requests, missing metadata, independent keys and evidence checks after retry.
These use local synthetic data and stub judges/adapters, not live filing or approval services.
`tests/test_pr_outcome_allowlist.py` covers complete positive lifecycle clauses, unknown verb classes
(wipe, transfer, email/send, publish, revoke), arbitrary tails, malformed/multiline diagnostics,
and bounded long-whitespace extraction. Filing tests exercise clause diagnostics on both tools.

Five whys for the outcome-scope gap: unknown verbs were accepted because outcomes used a finite
`EXTRA_AUTH` denylist instead of a complete positive grammar. Adding verbs only excluded known
examples, leaving every unnamed instruction in accepted scope. The action's fullmatch did not protect
these separately stripped outcomes. Earlier safety tests (`test_lifecycle_does_not_smuggle_authorization`)
covered named destructive verbs and repeated keys, not unknown operations; thus that test contract
could pass while the stated PR-only contract failed. The fix fullmatches each atom and tests unknown
verb classes on both outcomes, with filing-boundary diagnostics. Authority, owner, head, receipt and
CAS checks are unchanged; this parser still never grants permission.

The separate scope-field gap had the same validation-boundary cause: `_context` concatenated
scope solely for repository resolution, while `request_target` only screened the action and
its lifecycle suffix. Scope tests exercised resolution and ambiguity but not extra instructions.
`tests/test_pr_scope_allowlist.py` now covers the full positive grammar, unknown instructions,
label/length bounds, extra heads, URL tails, JSON, and scope-head non-authority;
`tests/test_pr_approval_filing.py` checks the scope diagnostic through both create and block hooks.
`tests/test_pr_scope_branch.py` covers verbatim dict/JSON label extraction, exact case-sensitive
branch matching, absent metadata, absent labels, and malformed-scope diagnostics. Slug syntax alone
cannot exclude imperative-looking labels; an earlier grammar discarded that label before consumers
could validate it. Consumers must bind it to the real PR branch rather than add a verb denylist.
Existing syntax tests did not exercise authoritative branch identity; the new helpers make that
separate semantic validation testable without adding I/O to this module.

### Cited evidence and vendor entitlements

Why (five whys): an owner decision (grant a shared GitHub App Administration:read, or make a dedicated App) was
refused four times. (1) The judge returned non-ok each time, suggesting a narrower token or the user's classic token.
(2) It never saw the proof: tool results were stored head-only at 400 chars and rendered at 220, and the HTTP 422
"permissions requested are not granted to this installation" and the 403 on the endpoint came after the echoed
permission JSON. (3) The prompt said a missing API permission plus a broader credential on the sheet is
agent_can_do "always", with no case for an App's own maximum permissions, which no token can exceed and GitHub has
no API to change. (4) The structured path refused on any non-ok verdict, so an invented scope ("github-minter")
refused just as a real one did; ordinary asks already discard ungrounded agent_can_do. (5) Nothing tested a long
result whose failure is at the end, or entitlement versus credential requests.

Fix: tool args/results are stored at 1600 chars and rendered at 220/360, keeping head and tail (`core.clip`). An
explicit request also gets the calls its evidence names (same matching as the evidence check, up to 6) at full
stored length. The structured prompt separates a missing credential (agent_can_do when a minter or broader scope
could issue it and nothing failed) from a missing vendor entitlement (ok when the cited results show the vendor
refusing the agent's own mint for that permission). It also rejects as `how` a retry of the failed mint, a
permission the evidence shows failing, or a credential the request's scope excludes. A structured agent_can_do whose
`how` names nothing on the sheet and no route used this turn is treated as ok, the same as for ordinary asks. A
grounded agent_can_do and every unverified verdict still refuse.

Tests: `tests/test_entitlement_evidence.py` (stub judge) covers tail survival in storage and rendering, the
cited-evidence section and its limit to cited calls, invented-scope handling, and refusal kept for a real minter,
for a route used this turn, and for unverified. Real-model accuracy is not covered by unit tests:
`scripts/replay_entitlement.py` replays one entitlement and two credential requests through the live judge (run
on hub 2026-10-29: 3/3 correct for each case).

### Superseding work (`superseding_work`)

Why (five whys): an approval card asked the user to fund on-demand CI hosts (+$500/month) while his own approved
migration to autoscaling runners sat blocked on the same board. (1) The card offered only spot vs on-demand. (2) Its
author looked at the current fleet, not at open cards on that subsystem. (3) Nothing at the ask surface made an
approval ask rule out superseding work: the evidence check verifies the claim, the overlap check matches duplicate
text only. (4) When the user then asked "but we just moved to EKS?", the agent searched his literal word, found
nothing, and replied "no sign that CI has moved". (5) Nothing questioned a reply that denies a change the user
named on a literal-term search.

Fix (`superseding.py`, deterministic, read-only, fails open):
- An explicit `human_request` with `interaction: approval` on `kanban_create`/`kanban_block`: topic tokens of title +
  action + scope (hyphen compounds split, ask boilerplate dropped) are scored against every board's open cards and
  cards done in the last 7 days (IDF over that set; >= 3 shared tokens and >= 18% weighted coverage; open cards
  first, at most 6). Before scoring, a candidate must share a non-workflow subject anchor from the ask title
  (scope when no title; full query when that supplies no informative anchors). CPU/GPU, parent, acceptance,
  execution, rerun and similar workflow words alone cannot establish relevance. Candidate bodies remain eligible:
  a migration need not use the same subsystem name in its title.
- Cards the ask already names by id (title, body, reason, evidence, why_human), the card being blocked, and the
  worker's own card are dropped. An unnamed list holds once, logged `superseding_held`. The displayed candidate
  snapshot is retained process-locally by session/tool/filing: create's idempotency key (title fallback), or block's
  task_id. Naming **all displayed IDs** on that filing releases this lint even after action/scope rewrites; it
  does not request the next six. An identical draft retry also passes. Independent filings and changed approvals
  without that reconciliation are still checked. The release (`superseding_released`) does not skip evidence,
  approval-store, retirement, judge, or core authorization/scientific gates.
- A final reply or `clarify` question that says "no sign / no evidence / found nothing / hasn't moved" is matched
  (with the user's last message) the same way and held once with the in-flight cards unless it names them.
- Limits: this remains a lexical advisory, not proof of supersession or approval. It can miss work using unrelated
  terminology. One-shot/snapshot state is process-local; preserve a stable filing identity across rewrites. A
  restarted process has no prior hold snapshot. Already-imported agent hooks do not hot-reload from a source pull.
- Kill switch: `ask_gate.superseding_work: false` in config, or env `ASK_GATE_SUPERSEDING_OFF=1`.

Regression root cause: workflow vocabulary could satisfy IDF coverage on busy boards. Following the notice while
rewriting action/scope changed the exact query hash, and excluding already-named cards revealed the next six.
The original retry test resubmitted identical arguments with only one relevant candidate, so neither the rewrite
nor pagination failure was exercised. Fix: distinct subject anchors plus bounded displayed-list reconciliation,
not a session-wide or task-wide exemption.

Tests: `tests/test_superseding_work.py` retains the original runner-migration create/block, cross-board,
named-card, retry, recent/old-done, denial/reply/clarify and kill-switch tests.
`tests/test_superseding_reconciliation.py` adds busy-board generic-overlap negatives, positive contract/body subject
matches, 14-candidate rewritten reconciliation, and independent new asks (including the same task). It also runs
real `model_tools.handle_function_call` direct and deferred `tool_call` dispatch through the registered gate hook,
with unrelated gate dependencies isolated and the execution sink/post-result hooks replaced so no synthetic
asks reach a live board.
Run `scripts/test-all -k ask-gate` from the repository root.

## Approval-store reconciliation adapters

Before judging an explicit `human_request` on create/block, `approval_reconciliation.preflight` consults
registered trusted adapters. A verified approval for the exact authority/subject/revision suppresses the repeat
ask; a veto stops the prompt without authorizing execution. Missing, stale, mismatched, malformed or unavailable
store evidence is agent-owned operational recovery, never inferred permission or a reason to demand approval.
An authoritative pending or new-scope result proceeds to the existing evidence/judge gate. Every attempt reads
again; transcript claims and model verdicts cannot replace store evidence.

Installed Python integrations call `approval_reconciliation.register(name, resolve, read)` and retain its disposer
for unload. `resolve(context)` returns `None` for unrelated actions or a mapping with exactly `authority`,
`subject_kind`, `subject`, `revision`. It must resolve the current full revision from its authority rather than trust
caller-supplied scope/head, and classify the actual action rather than the caller's `interaction` label. Real OS
password/physical authentication is distinct from approval. `read(target)` returns `{target, state, verified,
observed_at}`; `verified` must be literal `True` after source/provenance checks, and `observed_at` is the fresh read's
epoch timestamp (not the age of the approval). Maximum accepted read age is 30 seconds. `state` is `approved`,
`vetoed`, `pending`, or `new_scope`; the last requires an authoritative absence for the exact current scope, not
an older/mismatched approval. Readers must be bounded, read-only and return no secrets or raw principal evidence.
Adapter exceptions fail closed without exposing exception contents. Duplicate names are refused.

For deployment, set `plugins.entries.ask-gate.settings.approval_adapters` to a list of absolute Python file
paths in trusted private configuration. Each file exports `register(registry)` and calls
`registry.register(name, resolve, read)`. The gate passes its own registry instance, records every returned
disposer and removes adapters on plugin unload/reload. Do not separately import the hyphenated plugin package:
that creates a second registry which the real hook never consults. Configuration is executable trusted code,
not a tool argument or a place for model-provided paths. Broken configuration/import/registration rolls back
partial registrations and retains an operational hold on structured asks until configuration is repaired.
No adapters are enabled by default. `tests/test_approval_adapter_loading.py` exercises registration through
the plugin entry point, the actual structured hook, failure rollback, foreign registrations and unload/reload;
full live PluginManager discovery and real private adapters still require deployment verification.

This seam alone is not an installed approval integration: without a matching adapter the ordinary gate remains.
## Refused asks: hold first, then drop only the ask

When the final hook refuses an ask (closed user step, recorded terminal answer, stopped workflow,
unreconcilable or already-decided approval, or a negative judge verdict past its budget), it first
returns `{"hold": True, "nudge": ...}` while `attempt < budget`: the nudge names the refused ask and
the reason in words and tells the agent to resend without it, keeping its findings and the real
blocker. Once the budget is spent (or `hold_budget=0`) the reply is replaced by the draft minus the
refused ask sentences plus one plain note on why ("(I left a request out of this reply: ...)"). The
bare "I withheld a request ..." notice remains only when the ask cannot be cut out cleanly or the gate
errors after a rejection; coach's `withheld_notice_watch.py` files a Kanban card for any such
delivery. Tests: `tests/test_refusal_hold.py` (hold names ask + reason for approval-unreconciled,
approved, stopped and retired; budget-spent replacement keeps the findings for each; a resent draft
without the ask is delivered; an ask-only draft never leaks).

## Positive rejection survives reporting failures

Once the final hook has rejected a draft for stopped workflow, retired answer, authoritative
reconciliation, or a negative judge verdict, failures in unrelated verdict reporting cannot
release that draft. The response is replaced with a non-action notice. Before any positive
rejection, the existing judge-unavailable policy is unchanged.
`tests/test_f3_reporting_failure.py` reproduces corrupt reporting-cache shapes for all four
rejection paths and checks the non-rejection control. This does not prove old-core replacement
support or protection before a rejection has been established.

## Exact user-step source reconciliation (bounded integration)

`user_step_lifecycle.py` reads existing `::user-step` IDs through the adjacent user-steps
revisioned source reader, using the runtime profile and compression root rather than draft
ownership claims. Resolved state is durably latched for suppression, never authorization or
trusted reopen. Missing/corrupt/unknown source state holds for agent-owned recovery.
The final hook replaces already-framed requests before unrelated history/reporting can fail open.

Tests: `tests/test_user_step_lifecycle.py` exercises real source ledgers, profile/root isolation,
malformed directives, multiple IDs, rollback and final-hook suppression including exhausted budgets
and unrelated storage failures. This is NOT complete generated-step protection: ordinary user-steps
output transformation runs AFTER the final gate and creates new IDs/publishes cards. Source-owned
action identity continuity and a pre-publication reconciliation boundary remain required; no
plain-prose binding, trusted reopen or live deployment is claimed by these tests.

Adapters own current-head lookup, authenticated proof validation and domain-specific selection. No adapter may
mint a decision from a model claim or impersonate a user. The gate never calls decide, unblocks/completes a task,
or retires user-step metadata. Legacy prose and final-text wiring are not covered by this structured-hook slice.

`tests/test_approval_reconciliation.py` tests both create/block hooks with synthetic trusted adapters: exact approval,
veto, fresh repeated reads, scope/head/authority mismatch, stale/bad proof, unavailable store, agent-message claims,
interaction-label evasion, actual OS authentication exclusion and unload. These tests prove neither a live store
adapter nor Desktop rendering, natural-language authorization ingestion or execution completion.

## Model and config

Default judge: `claude-haiku-5-5` on the `anthropic` provider through Hermes' auxiliary client (task `ask_gate`), so
it rides the rotating Claude pool and needs no new key. Override with `auxiliary.ask_gate` in `config.yaml`, or
`ASK_GATE_PROVIDER` / `ASK_GATE_MODEL`. Typical latency 1-3 s per held-or-judged reply (the first call after a cold
start can take ~20 s); replies with no ask-shaped sentence never touch the model.

Off switch: `ASK_GATE_OFF=1`. Log of every verdict, hold and gate failure: `~/.hermes/logs/ask-gate.jsonl`.
Skipped: Kanban workers (for L1/L2), subagents, webhook platforms, and cron runs, except cron runs of a bot profile:
`ask_gate.bot_profiles` in `config.yaml` (default `[bb-tuner, coach]`; env `ASK_GATE_BOT_PROFILES=a,b`) names the
profiles whose scheduled reports go to the user, and L2 judges those like a chat reply. The hook registers with
`include_background = True` (otherwise core never calls it for cron), and `_gate_eligible` then lets through only
`platform == 'cron'` + a listed profile + not a Kanban worker + not a delegate_task child.

## Ask shapes (what `find_asks` catches)

The weekly regression cases also cover indirect "needs a manual nudge on the Kanban board"
handoffs and each numbered item under "Decision for you:". Explicit "None of them need you"
status reports are not asks. These are detection rules, not permission to bypass approval gates.

Beyond "Want me to...?" and "Needs you:": (a) an imperative to the reader: `please <verb>` always, and a bare leading verb only as a fallback/conditional
step ("In that case, check ...") or a chain of steps; plain imperatives that merely mention "you" are not asks (they
held 4.2% of replies). A line/sentence that starts
with open, check, tap, click, press, sign in, log in, go to, change, enable, disable, raise, switch/turn on/off,
install, run, delete (after an optional "Label:" and a leading "On the phone," clause), or `please <verb>`; skipped
when the user's last message asked how to do something (steps are then the answer) and capped at 8 per reply;
(b) a heading that names the reader (Your checklist, What I need from you, To confirm, To clear it, @user, For you)
makes every item under it its own ask; (c) incapability claims ("couldn't" is not matched: past-tense reports describe an attempt) (`I can't|cannot change|check|do|open|fix|reach`,
`Limitation:`, `would need ... doesn't have`), which stand only if this turn's tool log shows the attempt failing,
otherwise the verdict is `unverified` with no model call. An ungrounded `agent_can_do` on an imperative or "I can't"
ask is downgraded to `unverified` rather than `ok`, with a lower confidence floor (0.6).

Why (five whys, week of 2026-10-12: 6 corrections, 6 of the 8 causing replies had no detected ask): the user saw asks
that were not question-shaped; the detector keyed on question/"needs you" wording; that wording was copied from the first
incidents; nothing measured misses against the corrections the coach counted; so each new phrasing escaped. The 8 replies
are now fixtures (`tests/fixtures/week_1012.json`) and `scripts/replay.py` fails loudly if one stops being caught.

## Tests

```
cd plugins/ask-gate && uv run -q --with pytest python -m pytest -q -p no:cacheprovider tests
cd ../open-asks && uv run -q --with pytest python -m pytest -q -p no:cacheprovider tests
```

`tests/fixtures/session_20261021_103941.json` holds the four incident replies (I0 is the first summary, I1+I2 the
card message, I3, I4) and the good replies of the same session, scrubbed of personal names, addresses and domains.
Unit tests use a stub judge; `scripts/replay.py` runs the same fixtures through the real aux model (needs the Hermes
runtime interpreter, command in the script header) and `scripts/fp_report.py` runs the detector + judge over the last N
days of Desktop sessions in `state.db` to measure how many final replies would be held.

False-positive run (2026-10-21, last 14 days of Desktop sessions: 7,092 final replies, 1,388 with an ask-shaped
sentence): the gate would hold 93 replies = **1.31%** (target < 3%). Eyeballed: 21 clear catches, 40 debatable
(offers the agent could just do), 32 false positives (0.45% of all replies). Every hold is listed in
`~/.hermes/kanban/evidence/ask-gate/fp-report.md` (private: it quotes real chats). The judge is a model and
varies a little run to run; the deterministic backstops (live card status, ledger word match, the `Checked:` rule)
do not.

Known borderline: "Steps 3 and 5 need you to approve broader access once in Chrome." is judged `agent_can_do` when a
signed-in Chrome profile exists. The hold only asks the agent to verify or try first.

Chat-delivery false-positive scan (2026-10-30, every non-dependency/time/transient `blocked` event on the hub's
boards: 224 asks): `chat_delivery` matched 3, all real asks to relay text into a Hermes chat (runners
t_f15d182d, t_01a185154afb4293; default t_a5cc12d9). `tests/test_chat_delivery.py` pins those and the 4 real blocks a
first draft matched wrongly.
