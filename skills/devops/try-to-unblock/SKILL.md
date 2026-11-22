---
name: try-to-unblock
description: "Use when a card seems to need David. Try agent fixes first."
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
can clear it. When auditing an automated escalation, trace the actual producer: internal database calls do not
run tool pre-call hooks or load these skills. A timeout, quota failure, offline owner or absent subscriber proves
a delivery/recovery problem, not human-only authority. Keep agent custody and the original answer probe; never
copy an internal handoff into a human_request merely because the owning chat did not answer. The bar is **certainty**: if you are not sure David is needed, he is not. Do the work, apply your
recommendation and say how to reverse it.

Before any `action_request` approval for work in a goal David already approved, run rung a2/a3 first: a
no-deploy, no-spend build card for an approved design is self-unblocked, never an approval request.

Run this every time you are about to:
- publish an `action_request` approval/choice, or route a plan "to David" through another session;
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
that no longer exists.

A `⏸ … blocked` notice can be stale on arrival. It quotes its own event's reason and actor (an agent's CLI block
can read `actor: user`) and can land after a later clear, including your own metadata-only clear, which sends you
nothing. Compare the notice's reason with the card's newest `blocked` event and `human_request`. If a later event
superseded it, nothing is left to clear or ask: leave one trail comment.

Before writing an ask, look for one that already exists. A decision card the owner filed (named in the newest
comments, or a blocked card whose evidence cites this card) is the ask: point at it and never file a second one,
because two asks split David's answer. A live owner chat holds release: message it instead of asking David. Live
means an unreleased link of any kind (`created` counts) to a session with recent `last_activity_at`. Don't go by
`ended_at`: `ws_orphan_reap` leaves it set on chats still in use. Test each option you would offer against the card
first. Archiving or completing it promotes its waiting children, and re-scoping or promoting a safety-refused card
is refused.

If the premise is gone, remove that ask without releasing independent holds. A stale
human ask can coexist with a valid spend hold: use `action_request` withdrawal for its exact request ID, then
`hermes kanban block <id> --metadata-only --human-request null '<reason>'` to clear the card's human-request
metadata without authorizing execution. Read back both results. This does not change block kind or renew spend;
report the remaining hold accurately rather than promoting merely to make the status look clear.

A macOS permission ask (Screen Recording, Accessibility, "grant it at the Mac") is a premise to read, not to relay.
Read the Mac's existing grants first (query and how to read it: `references/macos-permission-grants.md`). Often
some client already holds the permission, so the card re-plans onto that client with no new grant. Never forward the
ask to David before this read.

## 3. Walk the self-unblock ladder

Stop at the first rung that works. Each rung is something agents may do without asking.

| Rung | Question | Typical resolution |
|---|---|---|
| a. Standing approval | Does an AGENTS.md rule, memory, or an earlier answer from David already cover this class of action? (e.g. "decide and proceed", merge-when-green, Hermes reversible fixes, GPU up to $25, "get all cards unblocked using your best judgement") | Apply it; cite the rule in the comment. |
| a2. Inside an approved goal | Is this a sub-plan, next phase or new approach for a goal David already approved, within its limits and exclusions (same budget, permissions, safety, acceptance)? Labels such as "needs separate approval" on a card, `executionAuthorized=false` on an earlier answer, a peer saying "I'll put it to David", a runtime nudge to "publish action_request", or a brief that says "on a miss, bring David the stop-or-continue decision" are NOT evidence he is needed. If you recommend Continue, apply it at this rung. Only a Stop recommendation (giving up the goal) or a change to acceptance or budget reaches rung g. Write follow-up briefs the same way: file the David ask only on a Stop or a change recommendation. | Get an independent review of the plan, then build it; tell David after, in one line. Don't publish an approval request. |
| a3. Next phase after a finished design | Is it "build what the approved design specifies" (an implementation card, tests only, on existing workers, no deploy, no new spend or authority)? An early "design first, not authorization to implement now" bounds the design phase; once the design is delivered and reviewed, building it inside those safety limits is the goal continuing, not new scope. | File the build card yourself with the design's limits as its exclusions; tell David in one line. Ask only if the build needs a deploy, spend, new permissions or a changed acceptance bar. |
| b. Agent-decidable pick | Is it a choice between options that stays inside the card's limits (no real money, credentials, write/admin permission, other people, safety, irreversible effect, acceptance threshold)? | Pick your recommendation, apply it, comment how to reverse. |
| c. Credential/access | Is it "no access"? | Token broker, Secrets Manager, hub/worker routes, elevated header (see AGENTS.md "Secrets"). Missing access is almost never a human step. |
| d. Do it differently | Does a different route reach the same acceptance without the blocked step (another host, a test fixture, a read-only proof, a scratch environment, a smaller scope)? Not for a provider safety refusal (`safety_refused`): rewording, promoting or splitting the same work into a new card is routing around it. Promote is refused, and the split card's worker stops it as `scope_stopped`. | Re-plan on the card and continue. On a safety refusal, go to rung g: David picks between applying for provider access and dropping that approach for a different design. |
| e. Someone else's work | Is it waiting on another card, CI, a deploy or a time? | `parents=[...]` or `kanban_schedule`, never a human block. |
| f. Human-only, but automatable handoff | Does it need David's hands only to type a password or click a dialog? | `run_in_user_terminal` / `remote_approve_dialog` / `browser_focus` (asking-the-user). That is a live step, not a parked card. |
| g. Scope/acceptance change | Does finishing require lowering the card's acceptance criteria, spending beyond budget, or giving up the goal? | Only this rung, and true human-only steps (physical device, his own re-auth, publishing as him, other people), justify a card. |

## 4. Decide

- **Resolved at rung a–f** → do it now. Comment on the card: `try-to-unblock: resolved at rung <x>: <what you did>;
  reverse with <how>`. Release only the resolved hold and read back state; preserve independent spend/safety holds.
  Retitle a resolved decision card to
  the work it now owns (`hermes kanban edit <id> --title …`): task notices and chat task lists quote the title, so a
  question title keeps reading as an open ask to David.
- **Rung g** → you are certain David is needed. Write down, for the next skill:
  - the one-sentence blocker (step 1);
  - what you checked (step 2) and what you tried and why each failed (step 3), in plain words: the card's
    why_human says "Tried: …", never "rung a/b/…" (rung letters belong only in your trail comment);
  - the decision David actually owns, phrased as a question he can answer without reading the card's history.
  Then load **generate-unblock-card** and file the card with it.

## 5. Leave a trail

Whatever the outcome, one comment on the card with the rung reached and the evidence (verbatim tool output, not
a paraphrase). The next agent and the ask-gate judge read it; without it they redo the ladder.

## Done means
- [ ] The blocker is one concrete sentence, and its premise was re-read live this turn.
- [ ] Every rung a–f was considered; the comment says why each one that applies failed.
- [ ] Readback proves either release, a valid filed human request, or removal of the stale ask while an
      independent hold remains. Distinguish metadata correction from execution authorization.
