---
name: generate-unblock-card
description: "Use when writing a human_request / blocked card for David, after try-to-unblock proved he is needed."
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
| `action` | One or two sentences: the step itself + "Unblock = <recommended>. Archive = <stop path>." Never "reply in this chat". |
| `why_human` | `Tried: <rungs from try-to-unblock and why each failed>. Only you can: <the human-only part>.` |
| `interaction` | `approval` / `reply` for a decision; `physical`, `user_terminal`, `external` for a step. |
| `scope` | The decision brief, three labelled parts (the panel renders them; the guard checks them): |
| | `Situation: <2-3 plain sentences>` |
| | `Options:` then one line per option: `A) <name> — <what happens>; <cost>; <reversible?>` |
| | `Recommend: <letter> — <one reason naming the tradeoff>` |
| `choices` | For decisions: 2-4 buttons, `{label, value}`, label ≤ 60 chars. Exactly one label ends `(recommended)`, listed first. |
| `evidence` | Verbatim tool output from this turn that shows the blocker (the guard refuses paraphrase). |
| `resolved_check` | Add when his step happens outside Hermes and a probe can see it (asking-the-user / physical asks). |

A physical or terminal step with no real alternatives still gets `Situation:` and `Recommend:` (what to do and
when); `Options:` may be the single step plus "Archive = drop it".

## 3. Read it back cold

Before filing, reread title + action + scope as if you had never seen this work:

- Could he answer from the panel alone, without opening the card? 
- Is every term understandable by someone who doesn't know Hermes internals?
- Does each option say what happens *and* what it costs him?
- Is the recommendation obvious and is the reason a real tradeoff?
- Do Unblock and Archive each do something he can predict?

Fix whatever fails, then file. The `unblock-card-quality` guard (ask-gate / owner-recommendation-guard) refuses a
request without `Tried:`, `Situation:`, `Options:` (decisions), `Recommend:` or exactly one `(recommended)`
choice; it can't judge clarity, so the cold read is yours.

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
  action: "Choose how to finish verifying the fix. Unblock = A (one supervised test run). Archive = B (accept with the known gaps)."
  why_human: "Tried: the original test app process exited, so I can't attach to it; restarting Hermes Desktop with debugging on is outside what you approved for this card. Only you can: allow a Desktop restart, or accept less proof than the card asked for."
  scope: |
    Situation: The fix for the Mac app's idle/frozen screen is merged and installed. Three of four checks passed. The last one (the screen recovers after a timeout while idle) needs a debug connection to the running app, and the app instance it was testing has since closed.
    Options:
    A) One supervised test run — I restart Hermes Desktop with debugging on (~1 min, your open chats reload), run the idle-timeout check, then restart it normally. Reversible.
    B) Accept with the known gap — lower the card's acceptance criteria to 3 of 4 checks and note the untested case. Nothing restarts; the gap stays untested until it shows up in normal use.
    Recommend: A — one minute of restart buys the only check that covers the bug you actually hit.
  choices:
    - {label: "A: one supervised test run (recommended)", value: "A"}
    - {label: "B: accept with the known gap", value: "B"}
  evidence: "<verbatim: ps/inspector probe output from this turn>"
```

## Done means
- [ ] try-to-unblock reached rung g, and `why_human` starts with what was tried.
- [ ] Title and Situation use no unexplained internal terms.
- [ ] Every option says what happens, what it costs, and whether it is reversible; one is recommended with a reason.
- [ ] Unblock and Archive each map to a stated option; nothing says "reply in this chat".
- [ ] The cold read passed before filing.
