---
name: kanban-red-sweep
description: "Use when asked to sweep red Kanban cards. Unblock, then ask."
version: 1.0.0
author: daveey
license: MIT
triggers:
  asks:
    - "sweep all red cards"
    - "there are a bunch of red cards, check them"
    - "unblock as many as you can, then ask me about the rest"
  not_for: "a digest of what waits on the user without acting (use kanban-hands-digest)"
metadata:
  hermes:
    tags: [kanban, blocked, unblock, sweep, orphan-cards]
    related_skills: [kanban-hands-digest, kanban-orchestrator]
---

# Kanban red-card sweep

"Red" = every card that waits on a human: `status='blocked'` (any `block_kind`), `gave_up`, and `triage` cards
with `block_kind` `needs_input`/`capability` (a block-loop moved them there). Sweep **all** of them on **every**
board, unblock what an agent can, and only then ask the user about the rest, in one form.

The user's standing order for this skill: do not ask before sweeping. Clear first, ask second.

## 1. Inventory (every board, read-only)

Boards: `$HERMES_HOME/kanban.db` plus `$HERMES_HOME/kanban/boards/*/kanban.db`.

```sql
select id,status,coalesce(block_kind,''),title,session_id,created_by from tasks
 where status in ('blocked','gave_up')
    or (status='triage' and coalesce(block_kind,'') in ('needs_input','capability'));
```

Per card, its chat ownership:
`select chat_gateway,chat_session,kind,released from task_chat_links where task_id=?`.
Live owner = an unreleased link whose session exists in that gateway's `state.db` `sessions`, is not `archived`, and
did not end with `end_reason='startup_orphan_reap'`. Report **unlinked** (no links, or all released) and
**reaped-only** cards separately: no chat will ever pick them up.

Then for each card read the latest ~6 `task_events` and the last 2 `task_comments`. The latest comment often
already says "resolved", "green again" or "replacement found". The block reason alone is stale evidence.

## 2. Classify each card. Re-test the premise live first.

| Finding | Action |
|---|---|
| Comment or live check shows the ask is already satisfied (PR merged, CI green, cert present, host back) | Verify the artifact yourself, comment the evidence, then `kanban_complete`/`hermes kanban complete`, or unblock so the owner finishes |
| Premise is false: account suspended or renamed, repo moved, PR closed, host retired, work superseded by a newer owner | Comment the evidence, archive it (superseded), or unblock it so the owner re-plans |
| Silent-default decision (`human_request` says "no answer = X") older than a few days | Apply the default: comment "silence = default X", then archive or complete as the card says |
| Ask covered by a standing approval (e.g. spend under the user's no-ask cap, fixing something broken) | Comment the standing rule, then unblock |
| Agent-doable from an interactive session but refused to a headless worker (guarded `rm -rf` of a verified scratch dir, a tailnet/ssh probe, a `gh` read the worker's profile lacked) | Do it here, verify it, comment, then complete or unblock |
| Missing credential on the worker profile | Find it with token-broker (`user/github` etc.) before calling it human, then do the step or unblock with the route |
| Real consent, policy, acceptance or scope decision; physical or sign-in step; protected-file Allow | Keep it. Link it to this chat, then ask (step 4) |

Never grant consent, change acceptance or approve protected paths on the user's behalf. A rejected guard is never
bypassed with another writer.

## 3. Act

- Unblock: `kanban_unblock` (or `hermes kanban unblock <id>` for triage/scheduled), always after a comment that says what changed.
- Completing a card with a PR completion contract runs `pr_acceptance` as **your** profile: wrap it with
  `token-broker exec --scope user/github -- hermes kanban complete ...` when `gh` isn't logged in.
- Cards whose dependents wait on them: unblocking or completing promotes the children. Check them.

## 4. Ask about the rest: one `clarify` form

- One question per remaining card, in plain household terms (what happens, not card mechanics). Recommended option
  first. No card ids, no "unblock = / archive =" wording.
- Batch low-stakes defaults into one "accept all recommended" question.
- Physical or sign-in steps are not questions. List them as actions grouped by device, each with what "done" looks like.
- Record each answer on the card yourself (comment, then unblock/archive/complete). Don't leave it to the user.

## Pitfalls

- `gh` on the hub default profile is not logged in. Use `token-broker exec --scope user/github -- gh ...`.
- Released links (`released=1`) look linked in a raw join. Filter `released=0`.
- A session ended by `startup_orphan_reap` still shows as unarchived but will never act. Treat it as no owner.
- The Desktop "For you" panel lists asks from every gateway. Cards that live on another gateway's board are read
  with `cross_task`, not this host's sqlite.
- Don't publish the "may I sweep?" question as an approval tile first: this skill is the approval.

## Done means
- [ ] Every board was inventoried, and the count per board and status was reported?
- [ ] Each red card was classified with a live premise check, not from the block reason alone?
- [ ] Every agent-clearable card was completed, archived or unblocked, each with an evidence comment?
- [ ] The remaining asks went out in one `clarify` form, recommended option first, with no card ids?
- [ ] Unlinked and reaped-only cards that still need the user are linked to this chat?
