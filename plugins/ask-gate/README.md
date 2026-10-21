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
| L3 | `pre_tool_call` | `kanban_create` with `initial_status=blocked` or a title/body saying the user must act, and `kanban_block` (not `dependency`/`time`/`transient`): refused unless the text has a `Checked: <tool or command> — <one line of output>` line that matches a tool call made earlier in the same turn (card tools never count as evidence). The judge then reads the body too. Kanban workers: `kanban_complete` handoffs with "David's call / David should / needs David" sentences are judged; any that is not judge-ok **and** backed by a matching `Checked:` line becomes `FYI (unverified, not an ask for David): …`. |
| L4 | ledger | `~/.hermes/state/ask-ledger.sqlite` (not synced). When the user's message answers or dismisses an open ask ("done", "i keep completing it", "stop asking", "you can do it") the aux model classifies it and the topic/answer is stored. L1 shows it in every chat (cross-session); L2's judge treats a repeat as `already_answered`. A cheap regex gates the classifier call, so unrelated messages cost nothing. |

Deterministic first: an ask naming a card that is `done`/`archived` in the database is `already_done` without any model call.

The judge sees: the ask sentences, this turn's tool calls and results, the last 30 user messages, live status of every
card id mentioned, the ledger, and a **capability sheet generated at runtime** (token-broker scope names and tiers —
no values —, ssh hosts from `~/.ssh/config`, signed-in Chrome profile emails from Chrome's Local State, browser vault
entry names). The sheet is built in memory, cached 10 minutes, and never written to disk or committed.

## Needs core hook `pre_final_gate`

L2 uses a core hook `pre_final_gate(session_id, platform, model, final_response, turn_messages, attempt) -> None |
{"hold": True, "nudge": str} | {"append": str}` (fork card t_45d5a361) so a held draft is neither shown nor saved. On
a core without it the plugin still registers (L1/L3/L4 work); L2 is simply never called.

## open-asks interplay

`open-asks` files a blocked card for an ask left in a reply. That would publish an unverified premise as a card. It now
checks `cache/ask-gate/verdicts.json` and files nothing when ask-gate held or could not verify the ask; with no record
(ask-gate absent) it behaves as before. Cards `open-asks` files itself are exempt from the L3 `Checked:` rule.

## Model and config

Default judge: `claude-haiku-5-5` on the `anthropic` provider through Hermes' auxiliary client (task `ask_gate`), so
it rides the rotating Claude pool and needs no new key. Override with `auxiliary.ask_gate` in `config.yaml`, or
`ASK_GATE_PROVIDER` / `ASK_GATE_MODEL`. Typical latency 1-3 s per held-or-judged reply (the first call after a cold
start can take ~20 s); replies with no ask-shaped sentence never touch the model.

Off switch: `ASK_GATE_OFF=1`. Log of every verdict, hold and gate failure: `~/.hermes/logs/ask-gate.jsonl`.
Skipped: Kanban workers (for L1/L2), subagents, cron/webhook platforms.

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

Known borderline: "Steps 3 and 5 need you to approve broader access once in Chrome." is judged `agent_can_do` when a
signed-in Chrome profile exists. The hold only asks the agent to verify or try first.
