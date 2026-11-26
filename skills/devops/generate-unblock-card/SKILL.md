---
name: generate-unblock-card
description: "Use when writing a human_request card. Make the ask clear."
version: 1.0.0
author: daveey
license: MIT
triggers:
  asks:
    - "this blocked card doesn't tell me what to do"
    - "rewrite this card's ask so I can actually decide"
    - "file a decision card for David"
  not_for: "deciding whether David is needed at all (use try-to-unblock first); a live sudo/sign-in step (asking-the-user)"
metadata:
  hermes:
    tags: [kanban, blocked, human_request, decision-card, unblock, presentation]
    related_skills: [try-to-unblock, kanban-orchestrator, asking-the-user]
---

# Write an unblock card David can answer in 30 seconds

Run **try-to-unblock** first. Only a rung-g result (certain that David is needed) gets here.

David reads these cards in a session summary on a small panel, between other work, with **no context**: he has
not read the card's history, the PR, or your run. The card must stand alone. If he has to open the card, ask
an agent what it means, or guess what a button does, the card failed.

Think it through before writing anything. Draft, then reread it as David cold and fix it.

## 1. Work out the content

Answer each in your notes first:

1. **What is this about, in his words?** The project/outcome he cares about, not the mechanism. "Testing the Mac
   app fix for the frozen sidebar (PR 1697)", not "PR1697 Mac delivery: guarded install, actual idle painted UI".
2. **What happened and why it stopped?** Two or three sentences. Explain or drop every internal term (PID,
   inspector, lane, drain, latch, patch number). If a term must stay, say what it is in five words.
3. **What exactly does he own?** The single decision or physical step. One question.
4. **What are the viable choices?** Two to four real options, including "stop / accept what we have" when it is
   real. For each: what happens next, what it costs him (minutes, money, risk, a restart), and whether it can be
   undone. Drop options nobody would pick; don't pad.
5. **What do you recommend, and why?** One option, one reason that names the tradeoff. If you have no
   recommendation you have not finished thinking; go back to 4.
6. **What does each button do?** Unblock = the recommended path. Archive = the stop/decline path. Other choices
   map to `choices[].value`.

## 2. Map it onto human_request (core schema; nothing else is stored)

| Field | What goes in it |
|---|---|
| card `title` | Plain outcome, ≤ 80 chars, no internal jargon: "Decide how to finish testing the Mac app fix (PR 1697)". |
| `action` | One or two sentences: the step itself + "Unblock = <recommended>; archive = <stop path>." Never "reply in this chat". |
| `why_human` | `Tried: <what you tried, in plain words>. Only you can: <the human-only part, one short clause>.` Never write "rung": the panel shows only the "Only you can" part; "Tried" folds under Details. |
| `interaction` | `approval` / `reply` for a decision; `physical`, `user_terminal`, `external` for a step. |
| `scope` | The decision brief, three labelled parts (the panel renders them; the guard checks them): |
| | `Situation: <at most 2 plain sentences, ~200 characters: what happened and why it stopped>` |
| | `Options:` then one line per option: `A) <name, ≤6 words>: <what happens>; <cost>; <reversible?>` (≤ ~140 chars after the name) |
| | `Recommend: <letter> - <one reason naming the tradeoff, ≤ ~120 chars>` |
| `choices` | For decisions: 2-4 buttons, `{label, value}`, label ≤ 60 chars. Exactly one label ends `(recommended)`, listed first. |
| `evidence` | Verbatim tool output from this turn that shows the blocker (the guard refuses paraphrase). |
| `resolved_check` | Add when his step happens outside Hermes and a probe can see it (asking-the-user / physical asks). |

Inside a Kanban worker, an `action_request` question is visible to David only through the card it is cited on. Cite each request id in the block's `human_request`, or better, use `human_request.choices`.

**`action_request` asks and chat decision lists follow the same rules.** A For-you ask (`action_request`), or a
numbered decision list in a chat reply (Hermes turns each item into an ask automatically), reaches David as a
bare title plus its choices. So:
- The title is the plain question with its outcome in it ("Flaky Hermes CI tests: ship 3 fixes (~$20), or drop
  the work?"). Never write "Decide <card name> <id>: ...".
- `scope.markdown` holds the same `Situation:` / `Options:` / `Recommend:` brief, plus one line saying what happens
  if he does nothing. No commit SHAs, run numbers or branch names.
- `choices` are real lettered buttons with `explain`. Never send only "Answer in your own words".
- Before asking, check his standing approvals (for example re-runs under $20). If they cover the pick, do it and
  don't ask.

A physical or terminal step with no real alternatives still gets `Situation:` and `Recommend:` (what to do and
when); `Options:` may be the single step plus "Archive = drop it".

## How the panel shows it

Load [session-status-panel](../session-status-panel/SKILL.md) for the PanelModel contract, input-to-field map,
fixture recipe and native verification. The backend's `plugins/session-panel/brief.py` is the single parser:
ask-gate uses its raw `sections()` / `options()` grammar and the reducer uses `parse_brief()` for presentation;
Desktop only draws the resulting model.

The card shows its title and Situation, then lettered option tiles with names, consequence details and the
recommended reason, followed by "Only you can: …". Tried, action mapping and evidence fold under Details.
When `choices` exist, **their labels supply tile order and names**; write `C: Test live (recommended)` to
match `C) Test live: ...` in scope. Own letters must be unique; missing/duplicate letters fall back to positional
badges. Scope details join by letter, not index, so C/A order is valid. Keep the explicit `Recommend: C - reason`
consistent with the single marked choice; it wins over the label marker. Put the reason in its first paragraph,
not an appended evidence/footer. The name after `A)` supplies the tile name only when choices are absent.
Keep decision-critical information in the brief, not only `action`. Exact line clamping and button styling are
renderer details, not ask-authoring requirements; a matching tile does not prove a remote answer was recorded.

## 3. Read it back cold

Before filing, reread title + action + scope as if you had never seen this work:

- Could he answer from the panel alone, without opening the card? Is the Situation two sentences or fewer, and does
  each option name read as a button label (≤6 words)?
- Is every term understandable by someone who doesn't know Hermes internals?
- Does each option say what happens *and* what it costs him?
- Is the recommendation obvious and is the reason a real tradeoff?
- Do Unblock and Archive each do something he can predict?

Fix whatever fails, then file. The `unblock_card_hold` (ask-gate, and owner-recommendation-guard on workers) refuses a request without
`Tried:`, `Situation:`, `Options:` (decisions: approval, any `choices`, or a reply asking for a pick),
`Recommend:` or exactly one `(recommended)` choice. Put each label at the start of its own line and letter the
options `A)`, `B)`. It can't judge clarity, so the cold read is yours.

**Stop-or-relax asks (`replan_hold`).** A card that ends an approved goal unmet, or offers a redesign or new
approach, is refused as a re-plan the agent owns. Before filing, get an independent feasibility review of
untried approaches within the unchanged limits; it can find one. If none fits, name the exact acceptance limit
the ask changes in `why_human` and the card body, for example "lowers the approved targets (links 0.2, memory
128 MiB) or ends the goal unmet". Never offer "redesign" as an option.

**An optional ask is offered once, ever.** AGENTS.md's offer-once rule (silence = default, never restated) is enforced by the ask-gate repeat hold, and it counts an ask you filed and later withdrew. A withdrawal comment must therefore state the default that now holds, never "this question comes back if X". The re-filing is refused (`repeat/completion lacks grounded subject/action equivalence`), and the worker was told to expect a change that never comes. Later numbers that favour the old recommendation are gate status in your report, not a new ask.

**Scope that is already a contract.** A PR approval (`scope` = `owner/repo#12 head abc1234`, pr-approval grammar)
or a physical ask (`DEVICE:` / `HUMAN_STEP:` / `OBSERVATION:` / `EVIDENCE:` lines) keeps that scope exactly; the
guard then asks only for `Tried:` and the single `(recommended)` choice. Put the situation, options and
recommendation in `action` and `why_human` instead, briefly.

## Worked example

Before (real card, 2026-11-20, rendered in the session panel):

> **PR1697 Mac delivery: guarded install, actual idle painted UI and live timeout recovery**
> Do this: Reply A or B in this chat. A = authorize one bounded repair run against the current Mac Desktop (PID
> 5198; you start it with a debug inspector, or tell me an inspector already exists). B = close acceptance with
> the documented gaps.
> Why you: The proven app process is gone and no inspector is available…

Problems: jargon title; "reply in this chat"; no situation; options don't say what happens or cost; no
recommendation; "PID 5198" and "debug inspector" unexplained.

After:

```yaml
title: "Decide how to finish testing the Mac app fix (PR 1697)"
human_request:
  interaction: approval
  action: "Choose how to finish verifying the fix. Unblock = A (one supervised test run); archive = B (accept with the known gap)."
  why_human: "Tried: attaching to the test app (it has exited) and checking without a restart (not possible). Only you can: allow a Desktop restart, or accept less proof."
  scope: |
    Situation: The Mac app's idle-screen fix is merged and installed; 3 of 4 checks passed. The last check needs a debug connection to the running app.
    Options:
    A) One supervised test run: restart Desktop with debugging while you're away; ~10 min, chats reload twice; reversible.
    B) Accept with the known gap: lowers acceptance to 3 of 4 checks; nothing restarts; reopen the card to undo.
    Recommend: A - two short reloads buy the only check that covers the bug you actually hit.
  choices:
    - {label: "A: one supervised test run (recommended)", value: "A"}
    - {label: "B: accept with the known gap", value: "B"}
  evidence: "<verbatim: ps/inspector probe output from this turn>"
```

## Done means
- [ ] try-to-unblock reached rung g, and `why_human` starts with what was tried.
- [ ] Title and Situation use no unexplained internal terms; Situation is two sentences or fewer.
- [ ] Each option is `A) <name ≤6 words>: <consequence>`; why_human never says "rung".
- [ ] Every option says what happens, what it costs, and whether it is reversible; one is recommended with a reason.
- [ ] Unblock and Archive each map to a stated option; nothing says "reply in this chat".
- [ ] The cold read passed before filing.
