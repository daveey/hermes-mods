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
judge errors, malformed/missing verdicts or non-ok verdicts. Ordinary final replies and legacy prose keep their
existing failure behavior; this is not a replacement for core authorization/security enforcement.

An initial blocked status, capability label, or `sudo -n` authentication failure alone is an operational issue,
not evidence that the user owes an approval. Keep it parked and owned without inventing a human request. Standing
authorization does not authenticate the OS. For a verified authentication step, use the supported user-terminal
flow with the precise command/host and step; never request passwords or secret codes/tokens in chat. Metadata
neither authorizes execution, bypasses security denials, completes work nor automatically unblocks a card.

`tests/test_human_request.py` exercises create/block hooks, required fields, supported interactions, unrelated
prose/evidence, operational failures, open-asks marker smuggling, judge outages, and ordinary clarification
preservation. Security prompt delivery and refusal handling are tested with a stub judge; real-model semantic
accuracy and live UI/REST/CLI paths are not established by these plugin unit tests.

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
