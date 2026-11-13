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

## Ready-card conditional evidence

For a non-blocked `kanban_create` without `human_request`, the legacy Checked-line classifier
scans the title and first paragraph. A short, comma-terminated `if`/`unless` antecedent in the
body is a condition, not a present human obligation: `if only David can submit, investigate`
does not turn agent investigation into a human ask. The consequent, title, and surrounding
text still gate (`if ready, David must approve`); malformed/unbounded conditions remain conservative.
Explicit blocked cards, `kanban_block`, and structured `human_request` validation are unchanged.
Coverage: `tests/test_ready_card_quotes.py` tests the classifier and `pre_tool_call`, including
positive obligations and unchanged blocked-card handling. This classification grants no execution authority.

## What it does

| Layer | Hook | Behavior |
|---|---|---|
| L1 | `pre_llm_call` | Injects a short block each turn: status of every card whose `tasks.session_id` is this chat (read straight from `kanban.db`, all boards) plus asks the user already answered (ledger). The list is every pinned card (open and blocked, in review or carrying a human_request, however old: never cut), then the chat's open cards newest first up to the cap of 25 (they rank ahead of every done/archived row), then finished cards filling what is left of the 15-newest window; open cards past the cap are not cut silently: the block ends with `N more open cards: run \`hermes kanban chat-tasks <sid>\``, and a `Waiting on David now:` section names each card blocked on him with the first 160 chars of its ask (self-heal agent-d9399023fc: in an 86-card chat the blocked card fell off a recency list). One sqlite read; silent when there is nothing to say. **Remote cards:** cards the chat placed on other gateways (placement=auto sends most to the hub or pool) are merged from core's chat projection (`kanban_session_activity.session_tasks`, what `hermes kanban chat-tasks` and the Desktop task panel read, `stale_ok`, over the chat's compression lineage), deduped by id and shown owner-qualified (`hub/default/default/t_…`); a remote card blocked with a human_request is pinned and listed under `Waiting on David now:`. The read is bounded (`REMOTE_TIMEOUT_S` 2 s in a daemon thread, never two at once: a turn that finds a read in flight waits for it within its own 2 s, then reads warm). A fresh process's first read is cold (2.5 s live), so `register()` starts `core.warm_projection` on a daemon thread (not in Kanban workers; `ASK_GATE_NO_PREWARM` turns it off) and logs `linked_remote_warm`; on an older core, an import failure, an error or a timeout the list is the local rows only and its heading says `local cards only` (`tests/test_l1_remote_cards.py`). **Watch parks:** a card parked `scheduled` with `kanban_schedule(watch=true)` is not the chat's open work, exactly as core counts it (core's own `hermes_cli.kanban_watch.not_watch_sql` predicate; an older core means no watches): it is never pinned, never in `N more open cards`, so that count matches `hermes kanban chat-tasks`, but a local one (on this gateway's boards) is always listed, outside the cap, marked `(watch: a pending check …)` so the agent names it instead of saying everything is finished; a remote watch card carries no watch flag in core's projection, so, as in `chat-tasks`, it is listed and counted as an ordinary open card (`tests/test_l1_watch_park.py`). L2 also holds, deterministically, a reply saying nothing waits on David ("not waiting for your approval") while a linked card is blocked on him with a human_request the reply doesn't name; logs `denied_wait_held`/`denied_wait_released` (`tests/test_l1_waiting_on_david.py`). |
| L2 | `pre_final_gate` | If the final reply contains an ask (`core.find_asks`: "needs you", "still waiting on you", "your call", "want me to…?", "can you…", "if you'd like me to…", "up to you", a "Needs you:" heading with its list), the judge returns a verdict per ask: `ok`, `already_done`, `already_answered`, `unverified`, `agent_can_do`. Anything but `ok` holds the reply with a private nudge naming the evidence. Two nudges max, then the reply is delivered with a short "could not verify" note. Judge down or garbled → deliver, log, never break a chat. |
| L3 | `pre_tool_call` | Explicit `human_request` on `kanban_create`/`kanban_block` is validated and judged as a complete structured request (see below), never via unrelated body/reason text. Status or operational failure alone is not an ask. Legacy prose explicitly saying the user must act retains its `Checked:` gate; worker `kanban_complete` handoffs retain the existing verified-ask/FYI rewrite. |
| L4 | ledger | `~/.hermes/state/ask-ledger.sqlite` (not synced). When the user's message answers or dismisses an open ask ("done", "i keep completing it", "stop asking", "you can do it") the aux model classifies it and the topic/answer is stored. Reads are scoped to the asking chat's compression lineage (never delegated children or unrelated chats); L1 shows the chat's own answers and L2's judge treats a repeat as `already_answered`. **Task binding:** an answer typed on a local single-user surface (`TASK_BINDING_PLATFORMS`: CLI, TUI, Desktop, local) to a shown ask naming exactly one card (`t_…`) is stored with that `card_id` (two or more ids, a multi-item heading unit, a general dismissal, or a turn from Slack/IRC/Telegram/API-server and other shared surfaces bind none). Any chat whose current ask explicitly names that whole card id (the `kanban_block` `task_id`, or the worker's own card when it is omitted; a `human_request`'s `action`/`scope`, not its `evidence`/`why_human`; an ActionRequest's `scope.sourceRefs`, not its plan text; ask sentences of a reply or prose card, not the rest of the reply) reads it in the judge context and in deterministic terminal retirement (still an exact action-text match), so sibling chats asking about one task get the same evidence. Within ask-gate's reads, unbound answers never leave their lineage; L1 context stays lineage-only (core's park refusal `kanban_awaiting.answered_ask` reads the ledger separately). Agent archives/comments never enter the ledger (`tests/test_task_answer_provenance.py`). A cheap regex gates the classifier call, so unrelated messages cost nothing. Only text the user typed counts: a turn whose row core stamped runtime-written (`display_kind` `kanban_notice`/`auto_continue`) or whose text opens like a runtime or agent relay (`[` or `<`, or a `✔ ✖ ⚠ ⏸ [` notice; `[chat-closeout]`, `[owner-wake]`, `[Message from another Hermes session …]`, `[System note: …]`) is never recorded, and every ledger read skips such rows written by an older process (`tests/test_runtime_notices.py`). |

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
`tests/test_weekly_deferrals.py` also covers the decision handback “When it completes, the user reads the
recommendation and decides the path forward” in both ask and leftover detection, and “tell me here directly”
in ask detection. These are candidates for the existing judge, not automatic refusals. Descriptive UI flows,
past decisions, and negated direct-reply requests remain negative controls.

**action_request approvals (L3).** user-steps `action_request` publishes an approval/choice card directly, so `pre_tool_call` judges each `kind: approval|choice` publish (never a `withdraw`) on its COMPLETE action identity (title, requestId/expectedRevision, limits, exclusions, outcomes, choices, source refs and the full scope markdown, so the verdict never depends on where in the plan the object or limits sit) against the user's typed messages from the WHOLE session (runtime notices dropped; the most relevant ones plus the newest 8, cut to the judge's 30-message window). Large-input policy (`AR_IDENTITY_CAP` 24000 chars): structured fields come first, each item clipped at 1500 chars and all together at half the cap; the plan fills the rest, head and tail kept around an explicit marker. Any clip or cut makes the identity incomplete, and a repeat verdict on an incomplete identity never holds (`action_request_repeat_unproven`; `agent_can_do` still does). The whole request is one judge item, so sentence screens (soft-instruction routing, the "I can't" short-circuit) do not run on its plan text; the irreversible keep does. A repeat for a request whose title + plan name more than a label must cite the request's object-bearing words, and a source that names a reused stage label ("run stage b") must share one of them: a reused "Stage B" is not equivalence. `already_answered` or `agent_can_do` blocks, quoting the verdict's own validated source evidence (or saying it is unavailable, never a word-overlap pick), and tells the agent to do the work; any other verdict, or no reachable judge, lets it publish. Held at most twice per (session, title). Log events: `action_request_held|judged|released|judge_unavailable|repeat_unproven`. Tests: `tests/test_action_request_gate.py`, `tests/test_action_request_scope.py`.

**Replan holds (L2/L3, no model call).** `replan.py` holds an ask that hands David "end the approved goal with its
target unmet/unproven, or expand scope / new scope / redesign / new approach" when it names no changed limit
(spend over an approved cap, a permission/access grant, the acceptance criteria or target, a safety/irreversible
step, or a human-only step). A new approach to the same approved goal is not new scope: the hold tells the agent to
write the new approach on the owner card, get an independent review and continue. Surfaces: `kanban_block` /
blocked `kanban_create` (`human_request` action + why_human, or a legacy needs_input reason; broad shape),
`clarify` and chat replies (strict shape: no bare "redesign"/"new approach", and a decision cue; an ask is read with
its paragraph). Naming the limit releases it; an unchanged ask is held twice, then passes (`replan_released`).
Logs `replan_hold` (with the card id), counted by `coach_askgate.py`'s weekly section. The judge's "choices between
options" ok no longer covers this shape. Why: self-heal agent-866a12f54f (babybear CI-cost card t_c21c170e asked
David to "end the project with its >=50% target unproven, or expand scope", relayed three times). Tests:
`tests/test_replan_hold.py` (incl. the $6 GPU over-cap and EBS resize asks passing).

**Agent-condition holds (L3, no model call).** `agent_condition.py` holds a `kanban_block` / blocked `kanban_create`
`human_request` whose action/why_human attributes the requirement to an agent (operator, orchestrator, creator,
parent card, card comment, steer, "my own condition", "self-imposed") with ask/approve/permission/prohibit/require
language, when no field quotes David's own words ("David said/asked/wrote/instructed ...", a user-step id `us_...`,
or a quoted line attributed to him). The hold tells the worker to apply its in-scope recommendation (an exact-head
label or approval is re-checked by its own gate) or ask the agent that set the condition on the card. Runs before
the PR-wording hold and the judge; an unchanged ask is held twice, then passes (`agent_condition_released`);
internal errors fail open (`agent_condition_error`). `STRUCTURED_SYSTEM` carries the same rule for prose the regex
misses. Logs `agent_condition_hold`. Why: self-heal agent-ad97832ad7 (an orchestrator's own "ask before any new
head" steer parked a test-only CI repair on David). Tests: `tests/test_agent_condition_hold.py`.

**Chat-delivery asks (L3, no model call).** `chat_delivery.py` refuses a `kanban_block`, blocked `kanban_create` or
worker `kanban_complete` leftover whose ask is for a human to type, send or relay a message into a Hermes chat: a
delivery verb (send, type, ask, tell, relay, resume…) aimed at an `@session:` ref, a `session <id>` or "the <name>
chat", or a structured ask naming a chat this turn's `cross_session` send was refused for. The refusal names the
exact `FINAL SEND for @session:<ref>: <text>` line (a worker's refused send subscribes the chat to its card) and
non-worker `cross_session` as the route. Not matched: "reply in this chat", "tell me", physical/external
`human_request`s, sentences that already name `cross_session`/`FINAL SEND`. Runs before the judge and fails open; a
refusal logs `chat_delivery_ask_refused`. Why: self-heal agent-c25865e208 (workers asked David for hours to type a
line into a repair chat that a FINAL SEND delivered).

**Chat-named terminal asks (L3, no model call).** A `user_terminal` `human_request` (on `kanban_block` or
`kanban_create`) whose `action` names a chat (an `@session:` ref, a bare session id, "owner chat" / "owner Desktop
chat") is refused with the canonical action `owner_terminal_action(<task>)`; chat refs go in `scope`. Logs
`terminal_action_names_chat`. Why: self-heal kanban-53e6157b2f (the named chat was reaped, the live chat deferred to
it, and the card sat blocked). `tests/test_named_owner_chat_action.py`.

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

Deterministic completion is limited to an exact stale target claim such as
`Still waiting on you: **t_1234abcd**.` whose card is done/archived. A card ID in prose or
`human_request.evidence` is contextual evidence, not proof that the requested action is complete.
Two shared ledger-topic words and numeric decision labels are likewise not action identity.

**Semantic repeat/completion checks.** Non-exact `already_answered` and `already_done` candidates
receive one additional model evaluation comparing subject, requested action, object and scope.
This runs on both ordinary asks and explicit structured requests, before the latter's refusal.
The model must cite a verbatim source quote, a quote from the indexed ask, and an explanation of
subject/action/scope correspondence in an `equivalence` object. Code checks the source category
and quote provenance; the model, not word overlap or caller claims, judges semantic equivalence.
Card context includes titles as well as status/results. Different labels and equivalent paraphrases
can still be repeats; a different action on the same topic need not be.

A corrected verdict is evaluated under the existing safety rules. An unsupported repeat becomes
`ok` on the ordinary fail-open path, but `unverified` with a safe correction on the structured path;
it does not automatically authorize that request. Non-repeat denials in a mixed batch are retained.
Malformed initial structured output remains rejected. An initial provider outage retains the existing
unavailable-judge handling; a failed recheck after a structured negative instead refuses as `unverified`,
never as `JudgeUnavailable`. Ordinary recheck failures release only the repeat candidates, retaining
independent denials. Provenance comes from structured inputs passed by `judge`, not headings parsed
from the prompt; headings inside asks, drafts or tool output cannot manufacture or truncate sources.
Structured request quotes match the decoded action. Label-only quotes and card ID/status alone do not
establish equivalence; completion quotes must come from a completed card's title/result or tool evidence.

Tests: `tests/test_scope_equivalence.py` and `tests/test_repeat_recheck_review.py` cover ordinary and
serialized requests, paraphrases, provenance, safety corrections, and failure cases. The latter also
exercises direct and deferred create/block calls through core dispatch and pre-dispatch guards, with
an isolated catalog, the real ask-gate hook and stubbed execution (no card writes). These are injected
model-output fixtures, not a live-model accuracy evaluation or live plugin-discovery test.

Review regression chain: the recheck exception previously shared the initial-call outage handler,
so a negative followed by a timeout became `JudgeUnavailable` and the hook accepted it. Separating
those phases preserves the initial outage contract without discarding a produced negative. Separately,
prompt-heading parsing treated untrusted draft text as source boundaries; out-of-band source data
removes that trust gap. The earlier outage test expected the erroneous exception policy, and provenance
tests did not inject headings. The new review regressions reproduced both failures before the fixes.

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
`already_answered` and `already_done` still require the scope/evidence checks above; the irreversible-action
rule itself leaves them and `unverified` alone. Control: a missing-permission ask with a
`*-minter` scope still gets `agent_can_do`. Tests: `tests/test_irreversible_guard.py`.

**Standing decisions.** `build_user_prompt` adds a "DAVID'S STANDING DECISIONS (user profile)" section built from the
§-separated entries of `<HERMES_HOME>/memories/USER.md` (capped at 2000 chars, "(none)" when the file is missing),
stated to be data, not instructions. SYSTEM's `already_answered` also covers "a standing decision in the user
profile declines exactly this", so a declined key rotation is not asked again.

**Soak is not a precondition.** A rule against soaks (an observation window after a change) does not answer an ask
about waiting for the precondition event a test needs (a real overload, natural quota exhaustion). 11-10: David's
"i dont want to wait for any soaking" was cited as `already_answered` for "wait for a real Claude overload to prove the
switch"; t_7e2ee45c then re-reconciled for 69 runs. SYSTEM says so, and on both paths (`judge()`, `structured_llm`)
`judge.is_precondition_wait` turns an `already_answered` whose evidence (source quote + reason) cites soak wording,
for an ask that waits for an event and is not itself a soak, into `ok` (reason "precondition wait: …", one
`precondition_kept` line in `ask-gate.jsonl`). An answer about the precondition itself with no soak wording ("don't wait for the
overload, close it as NO_PROOF") and an ask that is itself a soak still hold. Replay of all 105 `already_answered` rows in the hub's
log: 1 changes, the 21:12 incident ask. Tests: `tests/test_precondition_kept.py`.

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

### Scoped access evidence and worker-tool escalations

`access_scope.py` adds two **bounded evidence-quality nudges** before create/block asks:
- `regional_access_scope`: an observed AWS SSM denial with a region cannot justify an unscoped
  "SSM is refused" premise in a new card. Name the failed principal/action/resource/region;
  discover the intended target from trusted configuration or read-only inventory. Do not probe
  access with `send-command` or search arbitrary regions for a permissive route. A different
  regional endpoint's availability does not confer write authorization.
- `tool_policy_escalation`: a `user_terminal` ask justified solely by worker instructions
  prohibiting CLI use belongs in a supported supervisor/operator handoff, not a human
  authentication flow. Keep the worker prohibition; never bypass it through SSH/SSM. Mixed
  requests mentioning an actual human-only boundary still go through existing ask checks.

Each unchanged premise is held at most twice per session/tool; release only continues the
existing gates. These are not an execution guard, an AWS permission evaluator, or proof that
an operation is authorized. Historical corrections and precisely region-scoped denials are
allowed. Coverage is deliberately narrow (SSM denial wording and worker CLI-policy wording),
not arbitrary capability claims or every natural-language paraphrase.

**When recovering a mistaken ask:** read why it was filed and the actual consent/tool evidence.
An agent-generated `user_terminal` label alone neither proves that authentication is needed nor
provides execution consent. For real user restrictions/authentication, preserve the supported
flow. A notice, unblock, archive, or withdrawal is never execution consent. Recover already
agent-owned work only under independently established authority and supported tools, then
retire a stale ask with evidence; do not call retirement itself an approval or proof of success.

`tests/test_access_scope.py` reproduces the regional-overclaim filing path, precise scopes,
historical corrections and bounded release. `tests/test_tool_policy_escalation.py` reproduces
the unnecessary escalation and preserves human-boundary handling, authoritative pending
approval despite notices, and veto handling after nudge exhaustion. All use isolated fixtures;
they do not execute remote commands or establish live user-terminal authorization.

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
- A final reply or `clarify` question is checked only for explicit prospective spend/change decision language,
  not for denial vocabulary alone. Reports of decisions already made pass this lint. Neutral hold wording does
  not invent a claim about a missing migration. Replies/clarify share one hold per user turn, independent of the
  rewritten text; the next `pre_llm_call` turn resets that budget. Other authorization checks still apply.
- A reference can be a card ID, a 7+ hexadecimal commit prefix recorded in its title/body/result, or an
  unambiguous title phrase (three contiguous words or a distinctive hyphenated compound). Generic overlap and
  unrelated SHAs do not count. Title matching is lexical, not semantic paraphrase recognition.
- Limits: this remains a lexical advisory, not proof of supersession or approval. It can miss work using unrelated
  terminology. One-shot/snapshot state is process-local; preserve a stable filing identity across rewrites. A
  restarted process has no prior hold snapshot. Already-imported agent hooks do not hot-reload from a source pull.
- Kill switch: `ask_gate.superseding_work: false` in config, or env `ASK_GATE_SUPERSEDING_OFF=1`.

Regression root cause: workflow vocabulary could satisfy IDF coverage on busy boards. Following the notice while
rewriting action/scope changed the exact query hash, and excluding already-named cards revealed the next six.
The original retry test resubmitted identical arguments with only one relevant candidate, so neither the rewrite
nor pagination failure was exercised. Fix: distinct subject anchors plus bounded displayed-list reconciliation,
not a session-wide or task-wide exemption.

Reply regression root: the broad `found nothing` denial detector treated a completed review report as an ask;
its hard-coded notice invented a migration denial. Hashing the matched sentence let a rewrite hold again, and
ID-only exclusions ignored descriptive reconciliation. Earlier tests required holding a non-ask denial and
only retried identical wording. `tests/test_superseding_reply_scope.py` uses both exact rejected log excerpts
(full rejected drafts were not persisted), covers neutral notices, ID/SHA/title references, rewritten release,
next-turn reset, cross-surface release, and genuine omitted-work asks. These cases fail on the old implementation.

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
The recovery hold names the adapter and which step failed: `resolve` raising means the adapter could not
classify the ask and no store was read (fix its ask attribution: an adapter must return `None`, not raise,
for asks it cannot attribute to its own domain, e.g. another repo's "merge PR #N"); a `read` failure names
the check (read raised, unverified, stale, target mismatch, unknown state). Tests:
`test_recovery_names_adapter_and_failed_store_check`, `test_recovery_says_classification_failed_before_any_store_read`.

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

## Physical/sign-in ask admission

`physical_asks.py` runs before the structured-ask judge for `kanban_create` and `kanban_block`. Matching
`DEVICE:` + `EVIDENCE:` lines in `human_request.scope` identify a single act; an exact legacy action+scope
pair also matches. An open matching ask on any readable local board/profile returns `physical_ask_duplicate`
with its card and board, so the caller comments/links to that owner instead of filing another ask. Different
identifiers remain distinct; an owner may refresh itself. Reads fail closed on errors. No card is changed.

Build/file/waiter prerequisites (including explicit `PREREQUISITE:`/`EXPIRES:` labels) require a nonblank
`resolved_check`, otherwise `physical_ask_probe_required`. Core—not this plugin—executes it in the target
context before filing and while blocked. See the asking-the-user skill's `references/physical-asks.md` for
cross-gateway searches and expiry refresh without consent or physical-success inference. This is a local,
non-atomic preflight, not remote dedupe, proof of probe correctness, or automatic attachment/approval.

A hands-on ask that also names a passive observation window (normal use, overnight, "at least 20 hours", a soak)
or whose `resolved_check` waits on elapsed time returns `physical_ask_phases` unless scope separates the phases
with one `HUMAN_STEP:` and one `OBSERVATION:` line and the probe reads only the act's evidence. `HUMAN_STEP: done`
means no human ask remains: the hint is to `kanban_schedule` the still-open test. The guard never infers that the
act happened and never relaxes or completes the test (`tests/test_physical_phases.py`, full hook path with an `ok`
judge verdict).

Tests: `tests/test_physical_asks.py` covers both tool entry paths, cross-board/profile duplicates, padded
interactions, legacy identities, distinct device/evidence, closed owners, self-refresh, mandatory probes and
real core refusal of an already-met prerequisite. Remote races and live expired-waiter refresh are untested.

## Dropped resolved_check

When core stops probing an ask's `resolved_check` it stamps `human_request.resolved_check_dropped_at` and `resolved_check_drop_reason` (`resolved_check_broken` / `resolved_check_lost`). The L1 live-state digest and the denied-wait nudge then lead with "Automatic detection stopped ... needs a manual unblock until the ask is re-filed" and show the filed ask under a "historical wording" label (any "I will notice automatically" promise no longer applies). Older cores never write the keys; nothing is imported from core. Tests: `tests/test_dropped_resolved_check.py`.
