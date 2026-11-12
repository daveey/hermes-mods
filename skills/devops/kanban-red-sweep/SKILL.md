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

The user's standing order for this skill: do not ask before sweeping. Clear first, decide what you can
reasonably guess for him, and ask only about what you are really unsure of.

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
For a card from a recurring reporter, also list newer cards with the same title or `created_by`: a done sibling
may have settled the question. Then read the files it claims on disk, since a redeploy from the tracked source
can revert a worker's local edit after the card reported it.

## 2. Classify each card. Re-test the premise live first.

| Finding | Action |
|---|---|
| Comment or live check shows the ask is already satisfied (PR merged, CI green, cert present, host back) | Verify the artifact yourself, comment the evidence, then `kanban_complete`/`hermes kanban complete`, or unblock so the owner finishes |
| Premise is false: account suspended or renamed, repo moved, PR closed, host retired, work superseded by a newer owner | Comment the evidence, archive it (superseded), or unblock it so the owner re-plans |
| Silent-default decision (`human_request` says "no answer = X") older than a few days | Apply the default: comment "silence = default X", then archive or complete as the card says |
| Ask covered by a standing approval (e.g. spend under the user's no-ask cap, fixing something broken) | Comment the standing rule, then unblock |
| Agent-doable from an interactive session but refused to a headless worker (guarded `rm -rf` of a verified scratch dir, a tailnet/ssh probe, a `gh` read the worker's profile lacked) | Do it here, verify it, comment, then complete or unblock |
| Missing credential on the worker profile | Find it with token-broker (`user/github` etc.) before calling it human, then do the step or unblock with the route |
| Decision card (policy, acceptance, scope, review-tier approval) where you can make a reasonable guess at what the user would pick | Apply your recommendation: comment the choice, why you expect the user to pick it, and "applied under the red-sweep standing rule, not an explicit answer", then unblock. Report it so the user can reverse it |
| Decision where you are really unsure (options near a toss-up, hard to reverse, real money, touches other people, or no evidence of the user's preference); physical or sign-in step; protected-file Allow | Keep it and ask (step 4). If a live chat opened it in the last day, leave the ask to that chat |

The user's rule: for each card blocked on him, first ask yourself whether you can reasonably guess how he would
unblock it. He will usually just take your recommendation, so take it for him; ask only when really unsure.
A guess must still follow the card's own limits (scope, exclusions, normal review and landing gates). Physical and
sign-in steps and protected-file Allows are never guessed. A rejected guard is never bypassed with another writer.

## 3. Act

- Unblock: `kanban_unblock` (or `hermes kanban unblock <id>` for triage/scheduled), always after a comment that says what changed.
- A PR completion contract runs `pr_acceptance` with the **assignee profile's** `gh` login, not yours, so wrapping
  your command in token-broker doesn't help. Verify the merge yourself (`token-broker exec --scope user/github -- gh pr view`),
  then `hermes kanban edit <id> --completion-contract local-only --reason "<PR> merged at <sha>, verified"` and complete.
- `complete` is also checked against the card's acceptance. When it's refused because the acceptance still has open
  steps (live checks, a PR on a moved repo), comment the corrected premise and unblock so the owner finishes. Don't force it.
- A child can't complete before its blocked parent. When you've done the parent's work yourself, complete the parent first.
- Cards whose dependents wait on them: unblocking or completing promotes the children. Check them, queue position
  included: a promoted card keeps its old low priority and sits behind the queue you just cleared for its parent.
- A card you unblock may sit `ready` behind a full worker pool. Read the queue head first
  (`select id,priority from tasks where status='ready' order by priority desc limit 10`) and set
  `hermes kanban edit <id> --priority <above the head of that list>` in the same turn, for every card you
  unblock (guessed decisions too). A bump below the head (e.g. 11000 under a 20000+ head) leaves the card
  unstarted and the board flags it to the chat after 30 min. If the hub is full, move portable cards to a pool worker:
  `hermes kanban move <id> --runs-on machine:<worker> --reason "..."` (keep cards needing hub-only access,
  the Macs or another profile on the hub). `no_machine` for a worker usually means a Board hold; read its owner
  card before filing a fault. Free slots but nothing starting: check `~/.hermes/core-update/drain-hold` (a
  core-update drain pauses hub dispatch; `max_s` is not the end: `grep core-drain ~/.hermes/logs/hermes-sync.log`
  shows "claim hold kept past its bound" while workers run, up to a 2 h hard max, and no priority beats it, so
  move the card to a pool worker or do it here; before doing a fix card's work, `git log origin/main -- <file>`,
  since another PR may already have landed it) and `uptime` (admission refuses spawns while hub CPU is
  above `kanban.admission.cpu_busy_below_pct`; `ps --sort=-pcpu` names the job). A `ping_pong` move refusal means
  the card already left that machine: read its `moved` event reason (e.g. the worker lacks a hub-only tool) before
  forcing it back.
- A "ready, no worker" notice is not proof the card never started. Read its last events first
  (`select kind,payload from task_events where task_id=? and kind in ('spawned','worker_lost','oom_killed')`):
  after `oom_killed`/`worker_lost` a plain bump repeats the crash. Name the killed process from the kernel log
  (`journalctl -k | grep -i 'out of memory'`) and the last log file in the card's workspace, find the surviving
  worktree, and comment cause, worktree path and a capped first command for the next run, then bump.
- `no such task ... not imported here; last pickup skip: ...` on the hub means the card lives on the gateway that
  created it (a worker's run filed it pinned to the hub). `hermes kanban edit` here fails with "unknown id" and a
  twin `kanban_create` is refused `create_overlap`; set its priority on the origin box instead (`cross_task` read
  names the gateway; `aws ssm send-command` with `sudo -u ubuntu -i bash -lc "hermes kanban edit <id> --priority N"`,
  instance role, us-west-2). It still imports only when a hub slot frees.
- A `ready` card that already ran and has an open PR: before bumping it, read `hermes kanban show <id>`
  Diagnostics and the PR (`gh pr view <N> --json state,mergeStateStatus,statusCheckRollup`). A hold of
  `respawn_guarded: active_pr` keeps the card from restarting until its PR merges or closes, so no priority
  starts it; when the PR in turn waits for the card to land it, do the card's stated next step here (verify
  reviewed head = PR head and checks green, then submit it to that repo's landing path) and comment what you
  did and what is left. A red PR check: read the failed step's log and comment the exact failure on the card.
- A worker can't archive cards outside its own task; when its handoff names an exact archive/unblock command for
  sibling cards that follows the user's recorded answer, run it here, read back the status, and re-queue the worker.
- Link every card still waiting on the user to this chat, so its wake lands here:
  `hermes kanban [--board B] notify-subscribe <id> --platform tui --chat-id <this session id> --delivery-mode notify+wake`.
  The sync loop's chat-link reconcile turns that `tui` subscription into a `shepherd` row in `task_chat_links`
  within about a minute. Check `released=0`. A raw `INSERT INTO task_chat_links` is released again by the next
  reconcile, so don't write that table directly.
- Mac probes that a worker reports "no agent route" for (mini/MacBook check_compat, git ancestry, sqlite reads) often
  run directly with `tailnet_exec` from the user's interactive chat. A host outside `tailnet.auto_hosts` returns an
  approval request instead; name it in the report.
- Asks to delete a finished card's scratch workspace: core scratch GC removes terminal cards' scratch trees after
  `kanban.worktree_retention_days`. Comment that and archive; no human `rm` is needed.

## 4. Ask about the rest: `clarify`, no timeout

- Before asking, set `hermes config set agent.clarify_timeout 0`. The default 90 s timeout made the form vanish
  before the user answered, and he asked for forms with no timeout.
- One `clarify` call holds up to 5 questions. The user wants to answer many at once, so batch them: one
  self-contained question per card, numbered "N/total", plain household terms, the decisive fact in the question.
  Don't fold several decisions into one "accept all" question: the user wants a button per choice.
- Choices: the recommended option first, labelled "(recommended)", one button per real option, and a last "Skip"
  choice. Use no card ids in the labels and no "unblock = / archive =" wording. Free-text follow-ups (an allowlist,
  a consent reference) get their own open question.
- The ask-gate `superseding_work` check can refuse the form. Name the related cards it lists inside the question
  text (e.g. "(t_x only covered docs, so it doesn't settle this)"), then resend.
- Record each answer on its card right away (comment with the exact choice and the chat id, then unblock, archive
  or complete). Then withdraw every For-you request this chat published for the same question
  (`action_request {"withdraw": ...}`), so nothing stale stays in the queue.
  A skipped question gets the safest narrow default, stated in the comment.
- Don't re-publish unanswered questions as `session_outcome`/For-you tiles: they duplicate the form.
- Physical or sign-in steps are not questions. Before listing one, check it's still true (e.g. open the page in the
  signed-in browser); then list it with its device and what "done" looks like.

## 5. Log every red card (so fewer get blocked next time)

`R=~/.hermes/repos/hermes-mods/skills/devops/kanban-red-sweep/scripts/red_cards.py` (`~/code/hermes-mods/...` on the Macs).

- One line per red card, every sweep, whatever you did with it:
  `python3 $R log --card <id> --board <b> --title "<short>" --cls <class> --action <cleared|guessed|asked|left-to-owner|left-blocked> --avoidable <yes|partly|no> --slug <prevention-slug> --prevention "<what would have kept it from blocking>" --blocked-event <id from list> --run <chat or cron id>`.
  `--blocked-event` dedupes a card seen again in the same blocked state.
- `avoidable=yes` means the worker or the card's author could have resolved it without the user (a standing
  rule covered it, the premise was checkable, the default was stated, an interactive session had the access).
  Name the fix as a rule on the source (worker skill, card template, guard, reporter script), not on this card.
- Reuse a `--slug` verbatim when the same prevention recurs. `python3 $R stats` lists slugs; at 3 or more, file
  ONE prevention card for the slug (`idempotency_key='red-sweep-prevent:<slug>'`, body = the logged cards as
  evidence, the source to change, and a test that the next such card does not block). That card is the point
  of the log: the goal is fewer red cards, not faster sweeps.

## 6. Unattended runs (cron `kanban-red-sweep`, hub, every 30 min)

The cron's monitor is `red_cards.py list`: the agent wakes only when the set of red cards (or a card's blocked
event) changed, so cards that keep waiting on the user cost nothing. Cron `monitor`/`script` paths must be
a bare filename under `~/.hermes/scripts/`, so the job points at the thin wrapper `kanban_red_monitor.py`
there, which execs the skill script.

- Follow steps 1-3 and 5. Skip step 4: a cron run can't show a form. A card you are really unsure about stays
  blocked (its ask is already in the user's For-you queue); log it `--action left-blocked` and don't comment on
  it again unless its state changed. Don't re-link it to a chat.
- Read state first and record last with recurring-job-state (`job_state.py show|record kanban-red-sweep`).
- Final response: only decisions you guessed (card title, pick, how to reverse) and cards still waiting on the
  user, a few lines. Nothing cleared, guessed or newly waiting: reply `[SILENT]`.

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
- [ ] Each decision card was either unblocked on your recommendation (comment says it is a guess under the standing rule) or asked because you were really unsure?
- [ ] Cards still waiting on the user are linked via `notify-subscribe --platform tui`, read back `released=0`?
- [ ] The remaining asks went out as no-timeout `clarify` batches (recommended first, Skip last, no card ids), each answer recorded on its card, and stale For-you requests withdrawn?
- [ ] The report names the unlinked and reaped-only cards that still need the user?
- [ ] Every red card has a `red_cards.py log` line with class, action, avoidable and a prevention slug, and any slug at 3+ in `stats` has its one prevention card?
