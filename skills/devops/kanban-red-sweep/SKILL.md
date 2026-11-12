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

## 1. Inventory (every gateway, board and decision chat, read-only)

Run `scripts/red_cards.py list`. It includes local boards, Board `kanban/sync-shadow/*.db`
replicas (the cross_task read source; explicitly not live owner authority), and unarchived
`state.db` decision sessions with their open items, including non-card `us_*` asks.
Configure the monitor host's private `state/red-sweep-sources.json` as a gateway-name mapping
of `{"argv": ["approved-reader", "..."]}`. Each authorized unattended reader must return
`red_cards.py list --local-json` from that gateway. Discover the full gateway roster before
configuring it; include offline gateways, not only currently reachable ones. Never add SSH
credentials or enable a transport to evade an approval. Missing configuration and failed
readers print `unreachable ...`; neither means zero cards. Replicas can lag and do not prove
owner reachability. Check the roster against configured readers each sweep; an omitted gateway
is a coverage gap. A live local non-red row takes precedence over its stale replica.
For managed workers, check the monitor host's already-authorized instance-role SSM route before leaving a placeholder unreachable reader. Discover running instances by exact configured identity, require a unique match, and use GetCommandInvocation (ListCommandInvocations may be intentionally denied). A bounded read-only snapshot can ship a reviewed pinned collector when the worker's installed CLI is older. Keep the whole reader below the inventory caller timeout, bound delivery/execution too, compress stdout to stay within SSM's response cap, and fail closed on truncated output. Never grant new IAM access or restart a worker to make inventory green. Record gateway-local blocked-event IDs: replica event IDs are not owner event IDs.

Classify decision-session items alongside cards. Re-test stale `us_*` premises and withdraw
through the supported user-steps/action_request withdrawal route; never edit `state.db`.
Operate remote cards on their owner gateway with supported tools/courier. Cross_task reads
and (where live owner transport permits) comments; it does not unblock cards. Headless workers
must route missing host access to a capability owner rather than shelling around tool fences.

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
| Decision card (policy, scope, review-tier approval, acceptance that does not change a test or target) where you can make a reasonable guess at what the user would pick | Apply your recommendation: comment the choice, why you expect the user to pick it, and "applied under the red-sweep standing rule, not an explicit answer", then unblock. Report it so the user can reverse it |
| Decision that changes a fixed test, threshold or target after its result (waive a failed check, lower a target, swap or drop a check) | Never guessed, not even under a blanket "resolve them the way you know best": keep it and ask (step 4), recommendation first. Read the card's preregistered `IF FAIL <check>:` branch first; if it names an agent step (re-measure, report FAIL, file the decision), unblock so the owner follows it. A cron run logs it `left-blocked` |
| Decision where you are really unsure (options near a toss-up, hard to reverse, real money, touches other people, or no evidence of the user's preference); physical or sign-in step; protected-file Allow | Keep it and ask (step 4). If a live chat opened it in the last day, leave the ask to that chat |

The user's rule: for each card blocked on him, first ask yourself whether you can reasonably guess how he would
unblock it. He will usually just take your recommendation, so take it for him; ask only when really unsure.
A guess must still follow the card's own limits (scope, exclusions, normal review and landing gates). Physical and
sign-in steps, protected-file Allows and test/threshold/target changes are never guessed: a guessed waiver of a
failed check reaches the owner as if it were acceptance, and the
kanban-honest-done plugin refuses a worker handoff that waives a failed threshold without a body waiver David
gave (Gate G). A rejected guard is never bypassed with another writer.

## Prevent the decision block in the worker

Carry this rule into worker skills and card templates, not only the sweep: when evidence supports a recommended option inside the card's limits and normal review and landing gates, apply it rather than asking. Comment the pick and how to reverse it, then continue. Never bypass review or required CI.

Reserve a human block for real money above standing envelopes; permissions or credentials; other people; safety or privacy consent; hard-to-reverse effects; changing a fixed test, threshold or target after its result; or genuinely toss-up options with no evidence of the user's preference. A recommended choice is not permission to cross those boundaries. A block-time warning is advisory: a successful block still ends the run, and must not be followed by another lifecycle call.

Prevent acceptance-change surprises at card creation instead (slug `acceptance-waiver-owner-decision`): a card with a numeric gate states each threshold and preregisters, before execution and with independent review of the plan, one `IF FAIL <check>: <next step>` line per gate (re-measure once on fresh seeds, report FAIL and stop, file the decision card with options). A branch decides the next step; it never waives or passes the check. The worker then reports `metadata.acceptance_results` honestly (`fail` + the `branch` it followed) and the kanban-honest-done plugin's Gate G refuses a waived or guessed pass. When no branch fits, the honest outcome is a needs_input block naming the changed limit ("waives check 4", "lowers the 25% target"); that ask is legitimate, not avoidable.

For recurrence evidence, inspect the private red-sweep log for slug `owner-recommendation-not-applied`; keep personal incident details in the private worker skill rather than copying them into this public repository.

## 3. Act

- Unblock: `kanban_unblock` (or `hermes kanban unblock <id>` for triage/scheduled), always after a comment that says what changed.
  Any card comment (unblock, move, correction) cites only facts you checked this turn. Another card's "PR landed" is a
  self-report until `gh pr view <N> -R <repo> --json state,mergedAt` shows it merged, and the owner acts on your comment as fact.
- A PR completion contract runs `pr_acceptance` with the **assignee profile's** `gh` login, not yours, so wrapping
  your command in token-broker doesn't help. Verify the merge yourself (`token-broker exec --scope user/github -- gh pr view`),
  then `hermes kanban edit <id> --completion-contract local-only --reason "<PR> merged at <sha>, verified"` and complete.
- `complete` is also checked against the card's acceptance. When it's refused because the acceptance still has open
  steps (live checks, a PR on a moved repo), comment the corrected premise and unblock so the owner finishes. Don't force it.
- A child can't complete before its blocked parent. When you've done the parent's work yourself, complete the parent first.
- Cards whose dependents wait on them: unblocking or completing promotes the children. Check them, queue position
  included: a promoted card keeps its old low priority and sits behind the queue you just cleared for its parent.
  A child that is paused by decision and was only parked behind this card as a makeshift hold would restart: give
  it its own hold first (`hermes kanban schedule <child> --wake-check 'test -f <approval marker>' --check-every 3600
  --awaiting external:<decision> --awaiting-why "..." "<reason + how to resume>"`; awaiting kinds are card, ci,
  deploy, external, clock, human, resource), read back `scheduled`, then `hermes kanban unlink <parent> <child>`.
- A card you unblock may sit `ready` behind a full worker pool. First read where it really is: `hermes kanban show <id>`
  prints the dispatcher's own queue line (`waiting for a local slot: ~N ahead (k on other boards sharing these slots),
  r/limit running`). Don't go by a priority-sorted `ready` list: cards that other cards wait on run ahead of any
  priority, and boards on one host share its slots. Then, in the same turn, set
  `hermes kanban edit <id> --priority <above the ready head>` for every card you unblock (guessed decisions too).
  A bump below the head leaves the card unstarted, and the board flags it to the chat after 30 min. No priority
  gets past cards that others wait on: move a portable card instead, or do its step here. If the hub or a worker is full, move a portable card to the whole pool, never to one named worker:
  `hermes kanban move <id> --pool --reason "..."` (= `--runs-on pool:worker`; pool membership is the Board label
  `pool:worker`, on workers whose core has PR #1247). A `machine:<worker>` pin waits again as soon as that worker
  fills or is held. Pin one worker only
  when the card holds state there: an unpushed worktree, or the review card its ship reads (`hermes-core-wt` reads
  `--review-task` from the local board only); `--runs-on machine:<w> --host-reason "..."` records why. Moves are capped at 3 per card. Don't keep a card on the hub on an
  unchecked "only the hub can": pool workers carry Mac reach (`--runs-on capability:tailnet-exec`), hub reads and
  hub writes, so Mac-only work is portable. An assignee profile the pool doesn't run (e.g. `mac-ops`) pins the
  card to the hub and the move fails `capability_mismatch`: `hermes kanban assign <id> default` first, then move.
  A card created with `placement=local` or `requires host_repo` can't be un-pinned (`No move target satisfies
  required worker labels`; `kanban edit` can't change requirements): archive it unstarted and recreate it without the pin.
  If you do a queued card's work in the chat instead, comment the evidence and archive the card the same turn,
  or a worker repeats it. `no_machine` for a worker usually means a Board hold; read its owner
  card before filing a fault. Free slots but nothing starting: after the queue line, check `~/.hermes/core-update/drain-hold` (a
  core-update drain pauses hub dispatch; `max_s` is not the end: `grep core-drain ~/.hermes/logs/hermes-sync.log`
  shows "claim hold kept past its bound" while workers run, up to a 2 h hard max, and no priority beats it, so
  move the card to a pool worker or do it here; before doing a fix card's work, `git log origin/main -- <file>`,
  since another PR may already have landed it) and `uptime` (admission refuses spawns while hub CPU is
  above `kanban.admission.cpu_busy_below_pct`; `ps --sort=-pcpu` names the job. A `python3 -` with ppid 1 is an ssh-piped probe that outlived its client,
  because a local timeout kills only ssh: put `timeout N` inside the remote command of your own probes). A `ping_pong` move refusal means
  the card already left that machine: read its `moved` event reason (e.g. the worker lacks a hub-only tool) before
  forcing it back.
- A "ready, no worker" notice is not proof the card never started. Read its last events first
  (`select kind,payload from task_events where task_id=? and kind in ('spawned','worker_lost','oom_killed')`):
  after `oom_killed`/`worker_lost` a plain bump repeats the crash. Name the killed process from the kernel log
  (`journalctl -k | grep -i 'out of memory'`) and the last log file in the card's workspace, find the surviving
  worktree, and comment cause, worktree path and a capped first command for the next run, then bump.
- `no such task ... not imported here; last pickup skip: ...` on the hub means the card lives on the gateway that
  created it (a worker's run filed it pinned to the hub). `hermes kanban edit` here fails with "unknown id" and a
  plain twin `kanban_create` is refused `create_overlap`. Reach the origin box (`cross_task` read names the
  gateway; `aws ssm send-command` with `sudo -u ubuntu -i bash -lc "hermes kanban ..."`, instance role,
  us-west-2). A priority raise there is not enough when the skip count keeps climbing (top priority still sat
  unimported for an hour): replace the card. Create a hub-local twin (`placement='local'`,
  `distinct_from=[<id>]`, `distinct_reason` = it cannot import and is archived this turn, body = run the
  original's body with all its limits, first step = stop if the original started), then comment and
  `hermes kanban archive <id>` on the origin box and read back `archived`, so one owner remains.
  A hub card parented on such an unimported card sits `todo` forever (`parent_missing` in its events, parent
  absent from the hub's `tasks`): `kanban_unlink` it once its own acceptance is met or the twin exists.
- A `ready` card that already ran and has an open PR: before bumping it, read `hermes kanban show <id>`
  Diagnostics and the PR (`gh pr view <N> --json state,mergeStateStatus,statusCheckRollup`). A hold of
  `respawn_guarded: active_pr` keeps the card from restarting until its PR merges or closes, so no priority
  starts it; when the PR in turn waits for the card to land it, do the card's stated next step here (verify
  reviewed head = PR head and checks green, then submit it to that repo's landing path) and comment what you
  did and what is left. A red PR check: read the failed step's log and comment the exact failure on the card.
- After each completion notice, read what the chain's other cards wait on (`task_events` kind
  `scheduled`/`continued`/`dependency_wait`, payload `awaiting`/`reason`). A card on a timed park stays parked
  until `wake_at` even when the thing it awaits is over: the sibling card it named is done, or its CI run
  finished red (`gh run view <id> --json status,conclusion`). Comment the outcome (for red CI: failed jobs and
  test names from `--log-failed`) and `hermes kanban unblock <id>`, or its dependents wait on a dead timer.
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
  `python3 $R log --card <id> --gateway <owner> --board <b> --title "<short>" --cls <class> --action <cleared|guessed|asked|left-to-owner|left-blocked> --avoidable <yes|partly|no> --slug <prevention-slug> --prevention "<what would have kept it from blocking>" --blocked-event <id from list> --run <chat or cron id>`.
  `--blocked-event` dedupes a card seen again in the same blocked state.
- `avoidable=yes` means the worker or the card's author could have resolved it without the user (a standing
  rule covered it, the premise was checkable, the default was stated, an interactive session had the access).
  Name the fix as a rule on the source (worker skill, card template, guard, reporter script), not on this card.
- Reuse a `--slug` verbatim when the same prevention recurs. `python3 $R stats` lists slugs; at 3 or more, file
  ONE prevention card for the slug (`idempotency_key='red-sweep-prevent:<slug>'`, body = the logged cards as
  evidence, the source to change, and a test that the next such card does not block). That card is the point
  of the log: the goal is fewer red cards, not faster sweeps.

## 6. Unattended runs (cron `kanban-red-sweep`, hub, every 30 min)

Follow steps 1-3 and 5. Skip step 4, because a cron run can't show a form. Before acting, read
`references/unattended-runs.md`. It covers the monitor wrapper, left-blocked logging, job state, overdue runs
and the final response.

## Pitfalls

- `gh` on the hub default profile is not logged in. Use `token-broker exec --scope user/github -- gh ...`.
- `gh pr edit` can fail on a deprecated-Projects GraphQL error and change nothing. Use REST and read it back:
  `gh api -X PATCH repos/<o>/<r>/pulls/<N> --input body.json`, `gh api -X POST repos/<o>/<r>/issues/<N>/labels -f 'labels[]=<l>'`.
- Released links (`released=1`) look linked in a raw join. Filter `released=0`.
- A session ended by `startup_orphan_reap` still shows as unarchived but will never act. Treat it as no owner.
- The Desktop "For you" panel lists asks from every gateway. Cards that live on another gateway's board are read
  with `cross_task`, not this host's sqlite.
- Don't publish the "may I sweep?" question as an approval tile first: this skill is the approval.
- Write the sweep report as plain markdown. A `::user-step{...}` directive in a reply publishes its text as a
  For-you request, so a status table inside one becomes a fake ask that has to be withdrawn.
- "No red cards" from the hub's sqlite is not "no red chats". The sidebar paints a chat red from
  `sessions.outcome_json` kind `decision` (hub `state.db`), fed by blocked cards on any gateway (the MacBook board
  alone can hold many) and by chat-level asks (`us_*` item ids) that are not cards. `red_cards.py list`
  covers both; remote cards are commented and unblocked on their own host (`tailnet_exec ... hermes kanban
  comment/unblock`), `cross_task` only reads and comments.
- A chat still painted red after its cards finished: run `hermes sessions outcome <id> --refresh` on its gateway. It
  re-derives the outcome from live cards, so a decision about a done card becomes `done`. Never edit `state.db`.
- Chat-level asks (`us_*` extracted user-steps, For-you requests) can only be withdrawn by the chat that owns them;
  `action_request` withdraw acts on the calling session only. Send that chat (reaped ones too) a self-contained
  `cross_session send` that quotes the user's delegation and names the exact asks; it re-tests and withdraws. Read the reply.
- The For-you list injected at turn start can lag a withdrawal. The `withdraw` result's `state`/`withdrawal.at` is the
  live answer; check it before re-withdrawing or telling the user something waits on him.
- Priority-cap rollout (#1137): an upgraded host stores at most 1000 (`kanban edit --priority 60100` prints `Edited`
  and stores 1000), while un-migrated lanes still hold old values up to 60000+. A capped card can't get ahead there:
  move it to an idle machine (`kanban move --runs-on`); never raise it through an uncapped writer. Read back every edit.
- `kanban_create`/`move` refuse a machine on Board hold (`machine_held`). `hermes-mail admin machine-audit <m>` on the
  hub shows the hold reason and release time. A hold is deliberate, so never clear it to place a card.
- A sequential core rollout (one worker at a time) moves its hold to the next worker minutes after the last one
  lifts. Before moving or unblocking a card onto a worker, check that worker's hold live: `kanban_pickup_skips`
  rows with reason `hold` in the last 5 min, or `claim_deferred(held)` in its gateway.log `dispatcher stuck` line.
  Re-place ready cards pinned to a newly held worker. Leave cards whose worktree there has unpushed commits (their
  last `worker_result` says so): they resume when the hold lifts.
- "Pickup held on <m>" and "Pickup queue full on <m>" alarm cards are pinned to the stalled machine itself, so no agent
  works them while it is held or full. Re-place the waiting cards yourself. List `ready` cards whose `runs_on` names
  `machine:<m>` on each board. Find free slots on each target: `max_in_progress` in its `kanban:` config minus its
  running count. `kanban move` the portable ones to an unheld worker with free slots, then read back and comment why.
  Leave ship-stage cards whose exact-head approval was recorded on that host, because cross-host review custody can
  refuse the ship. Read the hold's owner card (rollout or core-drain) for its hard release time.
- An exact-head approval can be stale for landing. `hermes-core-wt ship` rebases, which makes a new head that the
  approval no longer covers, whenever main changed any PR file since the merge-base. Before shipping or re-placing an
  approved PR's card, intersect `git diff --name-only $(git merge-base HEAD origin/main) HEAD` with the same diff
  against `origin/main`. On overlap, the card needs a rebase plus a new review, so review custody no longer pins it to
  the old host: move it to a worker with free slots, and have the new review pinned to that worker.
- To find a PR's owner card, start from the review card the notice names: the owner is usually its child link
  (`cross_task read <review id>`, or the Mac's `kanban/sync-shadow/*.db`). Otherwise match its branch name
  (`headRefName`, e.g. `wt/<slug>`) and the card ids in the PR body against card bodies and comments on every host.
  Searching only for `PR<n>`/`#<n>` misses owners that published the PR after their body was written.
- Count a worker's free slots before moving cards to it: `max_in_progress` minus running, and check
  `kanban_pickup_skips` for recent `budget_exhausted` rows. One free slot with several contenders is not capacity.
- A burst of `rate_limited` runs (`HTTP 429 ... quota unavailable for openai-codex/...`) is not an outage by itself.
  Read the `run_route` events. A `tier_reason` of `default+wall:<provider>` or `+quota_reroute` means later spawns
  already go to another provider. A host whose core predates first-spawn wall routing wastes only the first spawn of
  each new card; the quota-wall alert card names the fix commit, so check it with `git merge-base --is-ancestor`. Count
  `gave_up` events and check the fallback pool's weekly headroom (resetwatch `probe.py --fresh`) before calling it harm.
- A card missing from a board isn't absent. Its row, events and run results live on the gateway that created it, not
  on the machine that ran it; `cross_task read <id>` finds it there.
- `notify-subscribe` from a Desktop chat warns "binding a different chat" and suggests `agent:main:local:dm:<id>`.
  Ignore that suggestion and keep the bare session id: following it writes a shepherd row for an id that is not a chat.
- A sweep report that relays an ask owned by another surface (BB Inbox, another chat) gets turned into a new For-you
  request owned by your chat by the user-step extractor. Right after the reply, withdraw it (`action_request` withdraw),
  giving a reason that names the owning surface, so the user isn't asked twice.
- An old ask missing what unblock and archive mean: amend it in place with `hermes kanban block <id> "<same reason>"
  --human-request "$(cat hr.json)" --metadata-only` (prints `Classified ... (metadata only; remains parked)`). Read the
  current human_request and block reason first (tasks row or last block event payload), so only the action changes.
- Before saying an ask is still blocked, re-read it: the user may have answered on another surface since the last read.

## Done means
- [ ] Every board was inventoried, and the count per board and status was reported?
- [ ] Each red card was classified with a live premise check, not from the block reason alone?
- [ ] Every agent-clearable card was completed, archived or unblocked, each with an evidence comment?
- [ ] Each decision card was either unblocked on your recommendation (comment says it is a guess under the standing rule) or asked because you were really unsure?
- [ ] Cards still waiting on the user are linked via `notify-subscribe --platform tui`, read back `released=0`?
- [ ] The remaining asks went out as no-timeout `clarify` batches (recommended first, Skip last, no card ids), each answer recorded on its card, and stale For-you requests withdrawn?
- [ ] The report names the unlinked and reaped-only cards that still need the user?
- [ ] Every red card has a `red_cards.py log` line with class, action, avoidable and a prevention slug, and any slug at 3+ in `stats` has its one prevention card?
