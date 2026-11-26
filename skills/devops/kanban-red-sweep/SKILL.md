---
name: kanban-red-sweep
description: "Use when asked to unblock red or not-ready Kanban cards."
version: 1.0.0
author: daveey
license: MIT
triggers:
  asks:
    - "sweep all red cards"
    - "unblock as many as you can, then ask me about the rest"
    - "there are 110 not-ready tasks, audit them and figure out how to unblock"
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

## 0. Scope first: red cards or not-ready cards?

Pick the column set from the user's words before running anything. Red cards and not-ready cards are different
audits, and answering one when he asked about the other is a miss even when every action is correct.
- **Red** ("red", "blocked", "waiting on me", "gave up", "needs input"): the rest of this skill.
- **Not ready** ("not-ready", "todo", "scheduled", "waiting", "parked", "why aren't these running", or a screenshot
  of the Board's Todo/Scheduled columns): the Board's **Todo + Scheduled** columns. Run
  `scripts/red_cards.py waiting` (`--json` for scripts). It reads the fleet replica
  (`kanban/sync-shadow/<board>.db`, which is what the Board shows) per board and gateway, and lists the actionable
  cards first: `sched-awaited-done` (wake it), `sched-overdue`, `todo-parents-done`, `todo-parent-missing`,
  `todo-no-parents` and `todo-parent-blocked`. Act on those (wake, relink, promote, or clear the red parent with
  steps 2-3), then summarise the rest by what they wait on (timer, card, CI/deploy, outside event, downstream of a
  wait). Lead the reply with the not-ready findings and the counts per gateway that reconcile with his number.
  Mention red cards in at most one line, and only when a not-ready chain is behind one. The red-card asks,
  decision forms and Done-means list below don't apply.
- Unsure which he means? Run both inventories (each is read-only and quick), and lead with the set whose count is
  closest to the number he quoted.

## 1. Inventory (every gateway, board and decision chat, read-only)

Run `scripts/red_cards.py list`. It includes local boards, Board `kanban/sync-shadow/*.db`
replicas (the cross_task read source; explicitly not live owner authority), and unarchived
`state.db` decision sessions with their open items, including non-card `us_*` asks.
Configure the monitor host's private `state/red-sweep-sources.json` as a gateway-name mapping
of `{"argv": ["approved-reader", "..."]}`. Each authorized unattended reader must return
`red_cards.py list --local-json` from that gateway. Discover the full gateway roster before
configuring it; include offline gateways, not only currently reachable ones. Never add SSH
credentials or enable a transport to evade an approval. Missing configuration and failed
readers print `unreachable ...`; neither means zero cards: read those gateways directly (and set up
managed-worker readers) per `references/remote-homed-cards.md`. Replicas can lag and do not prove
owner reachability. Check the roster against configured readers each sweep; an omitted gateway
is a coverage gap. A live local non-red row takes precedence over its stale replica.
Record gateway-local blocked-event IDs: replica event IDs are not owner event IDs.

Classify decision-session items alongside cards. Re-test stale `us_*` premises and withdraw
through the supported user-steps/action_request withdrawal route; never edit `state.db`.
Operate remote cards on their owner gateway with supported tools/courier. To answer a parked
card's decision, comment with `cross_task` (hub courier): a card waiting on its creator wakes on a
courier-delivered comment, and a comment written locally on the worker can leave it parked.
Unblock, promote, archive and edit still run on the owner host (`hub-worker-exec`); cross_task cannot. Headless workers
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
did not end with `end_reason='startup_orphan_reap'`. Any link kind counts (`created` too). `ended_at` alone doesn't mean
dead: `ws_orphan_reap` leaves it set on chats still in use, so read `last_activity_at`. When the card has a live owner,
or its newest comments name a decision card the owner already filed, file no ask of your own: name that chat or card
in the report. Report **unlinked** (no links, or all released) and
**reaped-only** cards separately: no chat will ever pick them up.

For a session-wide unblock, inspect every linked triage card, including spend holds without a human block_kind—not just the latest notice. Walk delivery prerequisites and read each blocker on its home gateway; do not expand to a fleet sweep unless requested. A prior release can be
superseded by a later STOP, while reviews on a parallel branch keep arriving. Record the actual blocking card,
restriction, renewal authority and next deliverable before acting; verify its new run after release. Report
parallel PR progress separately, never as evidence that the blocked delivery chain resumed.
For a card from a recurring reporter, also list newer cards with the same title or `created_by`: a done sibling
may have settled the question. Then read the files it claims on disk, since a redeploy from the tracked source
can revert a worker's local edit after the card reported it.

## 2. Classify each card. Re-test the premise live first.

| Finding | Action |
|---|---|
| Card owns an open core PR (base `main`, published by it), in any red or triage state | Spare it before any row below: never archive it; on its home board `hermes kanban spend-budget <id> --exempt --reason "<user's words>"`, then `promote` it if spend-triaged. The PR's fate stays with its owner (the user ruled core PR owners are never culled: an archived or spend-held owner orphans a written PR). The guard freeze bars new guard cards, not this. Find orphaned PRs from the full `gh pr list --base main --state open`, not from the cards that mention them: an owner that finished `done` without landing orphans its PR too |
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

Read `references/prevent-decision-blocks.md` (apply-the-recommendation rule, `IF FAIL` branches at creation).

## 3. Act

- Unblock: `kanban_unblock` (or `hermes kanban unblock <id>` for triage/scheduled), always after a comment that says what changed.
  Spend-held or scope-stopped card: from its owning chat, one step on its home gateway:
  `hermes kanban release <id> [--runs N] [--seen <newest comment id>] --note "<user's words + goal>"`, then read back status.
  Re-read events first: the board's spend reviewer may already have re-granted it, and release then refuses `not_held`.
  A refusal that prints an unread comment (`budget_decision_unread`, "After reading: ... --seen <cid>") wants that
  comment read and its id passed as `--seen`, not the newest id: an owner may have re-scoped in it.
  `safety_restricted` (provider safety refusal) has no release route: leave it held, never reword or resubmit the
  refused brief or switch provider to get past it; file a root-cause card on the release mechanism instead.
  Plain `triage` (no hold) also refuses `not_held`: use `hermes kanban [--board B] promote <id> "<reason>"`.
  A card you expect to stop again (a narrow run override against a multi-run rollout) gets a wider
  `hermes kanban spend-budget <id> --seen <cid> --runs N --cache-read-tokens T --reason ...` in the same turn.
  Its limits are window totals that include spend already recorded, so pass spent + the extra you mean (the refusal
  prints both numbers); a bare `--runs 30` below what's spent trips the card at once.
  A released card that then sits `ready` with workers logging `skipped_ineligible(refusal_exhausted)` has no
  unrefused provider left right now: on a worker, `kanban_provider_walls.active_walls()` shows which peers are
  walled and until when. It starts by itself when the wall ends, so comment that time; don't bump, move or release it again.
  `unblock` only re-triages a spend hold. Fallback without `release`: re-scope comment, `renew-scope --episode <stop
  uid> --mandate-comment <cid>`, then `promote --seen <cid>`.
  Release on a worker-homed card this chat created (run via `hub-worker-exec <w>`) is refused `not_scope_authority`
  until a human grants the chat rights there: run `hermes kanban delegate grant --session <this session id> --gateway
  <w> --profile default --reason "..."` through `run_in_user_terminal` (the user's blanket unblock permission covers it), then release.
  A card with no owning session (`origin_session` null) needs that grant or an operator.
  A triaged or failed card that a live card still lists as a parent gates that child forever: verify the outcome
  yourself (for a lander: log tail `bb land: exit 0`, `compare/<sha>...main` behind=0, equal source/landed trees),
  comment it on the child, `kanban_unlink` the dead parent, then archive it on its home gateway.
  A hub `todo` child whose parent is a worker-homed card that finished (completion notice in chat, or the
  worker's own board) stays gated while the hub copy lags: `promote` refuses `unsatisfied parent dependencies`.
  Comment the parent's verified outcome on the child, then `hermes kanban unlink <parent> <child>`; it goes `ready`
  at once. A REQUEST_CHANGES review parent counts as finished too: unlink it, or the author waits on a done review.
  A `triage` card whose newest lifecycle event is `contained` ends most "N on parent" chains with no parent visibly
  running or blocked (the panel counts it as "other"): walk open ancestors to it. Unblock/promote can't release it; only
  a live run of its event's `policy.authority` (or a new completed `policy.producer` run) can, via
  `kanban_resume_contained` with the episode uid (`tasks.containment_episode`). A `done` authority leaves it parked forever.
  Archiving the contained card does not free its children either (an archived contained parent still counts as
  unsatisfied) and `unlink` on its edges is refused (`containment_pending`). When the child's real wait is another
  card, archive the child with the reason and let its producer refile it waiting on that card directly. The refile
  is yours until a unique live replacement exists: tell the contained parent's owner chat at archive time that it
  is pending (its owner-wake otherwise opens a recovery card to refile it), confirm with the producer's dry-run,
  then send that chat the new id and its `awaiting` once the first run parks. A worker may park on a daily clock
  instead of the producer card: comment the card wait it should use.
  A burn-down cluster card is "dormant" only by the detector's fresh projection, never by the counts in its body or
  a worker's summary: check the bridge log for `using stale on-disk data` first, because the bridge refiles any
  cluster still `open` the moment its card is archived.
  Any card comment (unblock, move, correction) cites only facts you checked this turn. Another card's "PR landed" is a
  self-report until `gh pr view <N> -R <repo> --json state,mergedAt` shows it merged, and the owner acts on your comment as fact.
- A PR completion contract runs `pr_acceptance` with the **assignee profile's** `gh` login, not yours, so wrapping
  your command in token-broker doesn't help. Verify the merge yourself (`token-broker exec --scope user/github -- gh pr view`),
  then `hermes kanban edit <id> --completion-contract local-only --reason "<PR> merged at <sha>, verified"` and complete.
- `complete` is also checked against the card's acceptance. When it's refused because the acceptance still has open
  steps (live checks, a PR on a moved repo), comment the corrected premise and unblock so the owner finishes. Don't force it.
- A child can't complete before its blocked parent. When you've done the parent's work yourself, complete the parent first.
- Cards whose dependents wait on them: unblocking, completing or archiving promotes the children. When you drop a parent and its waiting child together, archive the child first: a pool worker claims a promoted card within seconds, and the archive then waits on its live lease. Check them, queue position
  included: a promoted card keeps its old low priority and sits behind the queue you just cleared for its parent.
  A child that is paused by decision and was only parked behind this card as a makeshift hold would restart: give
  it its own hold first (`hermes kanban schedule <child> --wake-check 'test -f <approval marker>' --check-every 3600
  --awaiting external:<decision> --awaiting-why "..." "<reason + how to resume>"`; awaiting kinds are card, ci,
  deploy, external, clock, human, resource), read back `scheduled`, then `hermes kanban unlink <parent> <child>`.
- A card you unblock may sit `ready` behind a full worker pool. First read where it really is: `hermes kanban show <id>`
  prints the dispatcher's own queue line (`waiting for a local slot: ~N ahead (k on other boards sharing these slots),
  r/limit running`). Don't go by a priority-sorted `ready` list: cards that other cards wait on run ahead of any
  priority, and boards on one host share its slots. For `matching_pool_busy` with recent same-label claims,
  retain placement and capabilities: a priority bump cannot repair a dependency or create capacity. Read the
  current priority before any justified edit; verify a claimed run separately from a successful edit. Move only
  a genuinely host-pinned portable card to the whole pool, never merely to another busy named worker:
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
  card before filing a fault. Free slots but nothing starting: after the queue line, grep the home gateway.log for `kanban dispatch held: <board>/<id>: start_gate:token_throttle` (the throughput plugin's fleet-wide token budget; `throttle.status()` shows rate vs cap): that is pacing, not a fault, and the card starts in the next gap under the cap, so don't bump or move it. Next, check that hub dispatch passes finish at all: `select datetime(max(end),'unixepoch') from kanban_suspend_ledger` (read-only kanban.db) is the last completed pass. If it's many minutes old, `sudo -n py-spy dump --nonblocking --pid <gateway MainPID>` a few times. A dispatcher thread (`_locked_tick`) parked in `write_txn`/`_execute_boundary_with_retry` is write-lock starvation (many gateway threads contending for kanban.db). Hub-pinned cards then get no claim and no skip event, and `dispatch --dry-run --json` only says `skipped_locked`. A gateway restart doesn't clear it, so put the stack evidence on the dispatcher-fix card, and move portable cards to the pool. Then check `~/.hermes/core-update/drain-hold` (a
  core-update drain pauses hub dispatch; `max_s` is not the end: `grep core-drain ~/.hermes/logs/hermes-sync.log`
  shows "claim hold kept past its bound" while workers run, up to a 2 h hard max, and no priority beats it. Pool
  workers hold the same way: `hub-worker-exec all -- bash -lc 'test -e ~/.hermes/core-update/drain-hold && echo HELD; grep "kanban dispatcher \[" ~/.hermes/logs/gateway.log | tail -2'`.
  The path is a symlink to `.drain-hold.<pid>.*.json` and is left dangling after the drain ends, so `ls` lists it
  and a hold looks live when it isn't; `test -e` follows the link. Recent `spawned=N` dispatcher lines mean there
  is no hold: the card is being passed over, so read the dispatcher's skip reason instead of reporting a hold.
  When every worker really holds at once, no claims happen while the scaler still reports free slots, so do the
  step here or wait. Otherwise
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
  created it: read `references/remote-homed-cards.md` (reach the origin box, or replace it with a hub-local twin).
- A `ready` card that already ran and has an open PR: before bumping it, read `hermes kanban show <id>`
  Diagnostics and the PR (`gh pr view <N> --json state,mergeStateStatus,statusCheckRollup`). A hold of
  `respawn_guarded: active_pr` keeps the card from restarting until its PR merges or closes, so no priority
  starts it; when the PR in turn waits for the card (e.g. its CI wake fired), run on the hub
  `hermes kanban resume <id> --reason "<why it continues THAT PR>"` (one continuation) and read back `running`. A red PR check: read the failed step's log and comment the exact failure on the card.
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
  reconcile, so don't write that table directly. Remote gateways refuse this chat's id (`not a chat in this
  profile's state.db`): name their waiting cards in the report instead.
- Mac probes that a worker reports "no agent route" for (mini/MacBook check_compat, git ancestry, sqlite reads) often
  run directly with `tailnet_exec` from the user's interactive chat. A host outside `tailnet.auto_hosts` returns an
  approval request instead; name it in the report.
- Review card gave up on `review_model_unavailable`, a `workspace_kind=dir but no workspace_path` spawn loop, or an ask to delete a scratch workspace: `references/gave-up-recipes.md`.

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
- Don't re-publish unanswered questions as `session_outcome`/For-you tiles: they duplicate the form. Relaying asks
  in a reply (form withdrawn by an update) is once only: each later `::user-step` restating them mints new requests.
- Physical or sign-in steps are not questions. Before listing one, check it's still true (e.g. open the page in the
  signed-in browser); then list it with its device and what "done" looks like.

## 5. Log every red card (so fewer get blocked next time)

`R=~/.hermes/repos/hermes-mods/skills/devops/kanban-red-sweep/scripts/red_cards.py` (`~/code/hermes-mods/...` on the Macs).

- One line per red card, every sweep, whatever you did with it:
  `python3 $R log --card <id> --gateway <owner> --board <b> --title "<short>" --cls <class> --action <cleared|guessed|asked|left-to-owner|left-blocked> --avoidable <yes|partly|no> --slug <prevention-slug> --prevention "<what would have kept it from blocking>" --blocked-event <id from list> --run <chat or cron id>`.
  `--blocked-event` dedupes a card seen again in the same blocked state. `--cls` takes the script's fixed names,
  not the table's wording: `already-satisfied`, `false-premise`, `silent-default`, `standing-approval`,
  `headless-refusal`, `missing-credential`, `conflicting-instructions`, `scope-permission`, `moving-target`,
  `review-reserved`, `real-decision`, `physical-or-signin`, `other`.
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

Read `references/pitfalls.md` before acting on this skill's steps (moved verbatim from SKILL.md).

## Done means
- [ ] If this chat created a cron or re-parked a linked card, does `hermes sessions outcome <id> --refresh --json` show no cron it still owns and no long-parked card it waits on (or the reply says which one and until when)?
- [ ] Scope matched his words (step 0): a not-ready/Todo/Scheduled ask got the `waiting` audit at the top of the reply, not a red sweep?
- [ ] Every board was inventoried, and the count per board and status was reported?
- [ ] Each red card was classified with a live premise check, not from the block reason alone?
- [ ] Every agent-clearable card was completed, archived or unblocked, each with an evidence comment, and every open core PR (bots aside) still has a live owner card that isn't spend-triaged, by a census that read archived rows too (`references/pr-owner-cards.md`)?
- [ ] Each decision card was either unblocked on your recommendation (comment says it is a guess under the standing rule) or asked because you were really unsure?
- [ ] Cards still waiting on the user are linked via `notify-subscribe --platform tui`, read back `released=0`?
- [ ] The remaining asks went out as no-timeout `clarify` batches (recommended first, Skip last, no card ids), each answer recorded on its card, and stale For-you requests withdrawn?
- [ ] The report names the unlinked and reaped-only cards that still need the user, and every status and count in it was read on its home board this turn (he may have answered since), earlier-turn writes worded as earlier (coach `claimed_mutation` rejects older write claims)?
- [ ] Every red card has a `red_cards.py log` line with class, action, avoidable and a prevention slug, and any slug at 3+ in `stats` has its one prevention card?
