---
name: try-to-unblock
description: "Use before blocking a Kanban card on David, filing a human_request, or relaying a blocked card's ask."
version: 1.0.0
author: daveey
license: MIT
triggers:
  asks:
    - "this card is blocked on me, can you just handle it?"
    - "get all the cards here unblocked using your best judgement"
    - "why is this waiting on me?"
  not_for: "writing the card once escalation is decided (use generate-unblock-card); a card waiting on another card or a schedule (parents / kanban_schedule)"
metadata:
  hermes:
    tags: [kanban, blocked, human_request, unblock, escalation, decision]
    related_skills: [generate-unblock-card, kanban-orchestrator, kanban-worker, asking-the-user]
---

# Try to unblock it yourself first

A blocked card costs David attention every time a summary renders it. Before one exists, prove that no agent
can clear it. The bar is **certainty**: if you are not sure David is needed, he is not. Do the work, apply your
recommendation and say how to reverse it.

Run this every time you are about to:
- call `kanban_block` with a `human_request`, or `kanban_create(initial_status="blocked", human_request=...)`;
- relay or re-file an existing blocked card's ask (a session summary, a red sweep, a "Needs you" list);
- end a turn saying something "needs David".

Spend the thinking here. Write the reasoning down in the card's comments (step 5), not in your head.

## 1. Restate the blocker in one sentence

"The card cannot finish because ___." Name the concrete missing thing: a credential, a click on a specific
machine, a choice between named options, money, a permission, another person. If you cannot fill the blank with
something concrete, the card is not blocked on a human. It is unclear work. Re-plan it, don't escalate it.

## 2. Check the premise is still true (live, not from memory)

Read the card's newest comments and runs, the linked PR/CI, and the actual state the blocker refers to (process
alive? file present? token valid? PR merged?). Many asks are already answered, already done, or about a process
that no longer exists. If the premise is gone, the block is gone: comment what you read and unblock.

## 3. Walk the self-unblock ladder

Stop at the first rung that works. Each rung is something agents may do without asking.

| Rung | Question | Typical resolution |
|---|---|---|
| a. Standing approval | Does an AGENTS.md rule, memory, or an earlier answer from David already cover this class of action? (e.g. "decide and proceed", merge-when-green, Hermes reversible fixes, GPU up to $25, "get all cards unblocked using your best judgement") | Apply it; cite the rule in the comment. |
| b. Agent-decidable pick | Is it a choice between options that stays inside the card's limits (no real money, credentials, write/admin permission, other people, safety, irreversible effect, acceptance threshold)? | Pick your recommendation, apply it, comment how to reverse. |
| c. Credential/access | Is it "no access"? | Token broker, Secrets Manager, hub/worker routes, elevated header (see AGENTS.md "Secrets"). Missing access is almost never a human step. |
| d. Do it differently | Does a different route reach the same acceptance without the blocked step (another host, a test fixture, a read-only proof, a scratch environment, a smaller scope)? | Re-plan on the card and continue. |
| e. Someone else's work | Is it waiting on another card, CI, a deploy or a time? | `parents=[...]` or `kanban_schedule`, never a human block. |
| f. Human-only, but automatable handoff | Does it need David's hands only to type a password or click a dialog? | `run_in_user_terminal` / `remote_approve_dialog` / `browser_focus` (asking-the-user). That is a live step, not a parked card. |
| g. Scope/acceptance change | Does finishing require lowering the card's acceptance criteria, spending beyond budget, or giving up the goal? | Only this rung, and true human-only steps (physical device, his own re-auth, publishing as him, other people), justify a card. |

## 4. Decide

- **Resolved at rung a–f** → do it now. Comment on the card: `try-to-unblock: resolved at rung <x>: <what you did>;
  reverse with <how>`. Unblock it (or never block it) and read the status back.
- **Rung g** → you are certain David is needed. Write down, for the next skill:
  - the one-sentence blocker (step 1);
  - what you checked (step 2) and what you tried and why each failed (step 3), in plain words: the card's
    why_human says "Tried: …", never "rung a/b/…" (rung letters belong only in your trail comment);
  - the decision David actually owns, phrased as a question he can answer without reading the card's history.
  Then load **generate-unblock-card** and file the card with it.

If you are relaying an existing blocked card (summary, sweep) whose ask is unclear or that rung a–f resolves,
don't relay it: resolve it, or rewrite its `human_request` with generate-unblock-card (`kanban_block` again on
the same card with the new request) before it reaches David.

## 5. Leave a trail

Whatever the outcome, one comment on the card with the rung reached and the evidence (verbatim tool output, not
a paraphrase). The next agent and the ask-gate judge read it; without it they redo the ladder.

## Done means
- [ ] The blocker is one concrete sentence, and its premise was re-read live this turn.
- [ ] Every rung a–f was considered; the comment says why each one that applies failed.
- [ ] Either the card is unblocked (status read back) or generate-unblock-card filed the request, never a bare
      "needs David" in chat or a `kanban_block` reason.
