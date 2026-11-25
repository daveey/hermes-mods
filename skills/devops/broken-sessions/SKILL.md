---
name: broken-sessions
description: "Use when a chat/session is stuck, broken or waiting oddly, or when the user shares an example of one."
version: 1.0.0
author: daveey
license: MIT
triggers:
  asks:
    - "this session is stuck"
    - "why is this chat still waiting? a worker is free"
    - "this card says waiting but nothing is running it"
    - "here's another broken session, add it"
    - "the panel says overdue / no wake published / no reviewer running"
  not_for: "a red-card sweep of everything waiting on the user (kanban-red-sweep); one card with many runs (kanban-card-churn)"
metadata:
  hermes:
    tags: [kanban, sessions, stuck, repair, scheduled, wake, dispatcher, notices]
    related_skills: [kanban-red-sweep, kanban-card-churn, try-to-unblock, generate-unblock-card, hermes-workers, five-whys]
---

# Broken sessions: the catalog

A broken chat has work no active producer can resolve. `plugins/session-repair` in hermes-mods scans these
`BS-...` patterns every 15 min on the hub and sends findings to **Session repair**.

Run the scanner for a stuck chat; follow the finding's fix section. If none matches, collect evidence before adding a pattern.
- **Panel says "needs you" / blocked on the user, but no one asked anything**: check what core says before
  touching the card. Core's verdict is the session feed's `wait_state`/`waiting_on` (`hermes_cli.kanban_session_activity.session_tasks`)
  and, for scheduled cards, `kanban_wake_summary.wakes(conn, task)["blocks_for_human"]`. If core says agent-owned
  or external while the panel says human, the fault is in the panel, not the card. Panels re-derive "needs you"
  from the raw `block_kind`, but core keeps `block_kind` on purpose after an unblock or resolved ask, so it records
  past blocks, not the current wait. Fix the client so it uses core's verdict, and add the card as a regression
  fixture. Never clear `block_kind` by hand.
- **Row says "Running" with nothing running, or a wake probe looks broken**: `references/running-label-and-probes.md`.
- **"Chat isn't accepting my messages"**: this is a transport problem and the scanner can't see it. Follow `references/message-not-delivered.md`.

```sh
# read-only; on the hub (where the boards live), with core's Python. Prints {findings: [...], errors: [...]}
nice ~/.hermes/hermes-agent/.venv/bin/python ~/.hermes/plugins/session-repair/scan.py   # every chat with an open linked card
nice ~/.hermes/hermes-agent/.venv/bin/python ~/.hermes/plugins/session-repair/scan.py --rule BS-overdue-wake
curl -s localhost:<dashboard port>/api/plugins/session-repair/findings?chat=<session id>   # last cron scan
```

When asked why waiting chats aren't being cleared, read `$HERMES_HOME/session-repair/config.json` first: `mode`
`shadow` means the cron only logs would-act rows (`actions-shadow.jsonl`) and fixes nothing. `live_rules`
(`["<id>:<variant>", ...]`) limits live mode to those findings; the rest stay shadow; absent = all live,
malformed = nothing live. Set it with `install.py --apply --mode live --live-rules A:x,B:y` (`--live-rules all`
clears it). This turns on the safe fixes (`clock_overdue`, `awaited_done`, `free_slot`) without waiting for the
whole gate.

For a shadow-to-live gate, use `$HERMES_HOME/session-repair/ticks.jsonl` plus its rotated `.1` segment, not
cron output history (which can retain less than 24 hours). Require a full same-build shadow window with zero
`error_count`, scanned ticks without unexplained gaps/skips, and adjudicate every would-act row from
`actions-shadow.jsonl`. A missing journal or zero repair turns is not clean-shadow evidence.

Every finding carries `id`, `variant`, `card`, `board`, `chat`, `evidence`, `fix {kind, action, commands, why}`,
`area` and `fingerprint` (`<id>:<board>/<card>`, or `<id>:chat/<session>`); the cron adds `idempotency_key`.
Fix kinds: `auto` = safe and reversible, the repair chat applies it; `card` = needs real work, file a fix card.
`area`: `hermes` = Hermes machinery misbehaved (dispatcher, wakes, leases, notices); `card` = the card owner's own
work. Never judged: the Session repair chat itself (its `chat.json` id and any chat with that title), cards the
repair session created (idempotency key `session-repair:...`, a card the turns reported as outcome `card` in
`ledger-live.jsonl`, or the card the self-work intake created for a request they reported as `queued` or that carried
a `session-repair:` key; a card they only commented on, fixed or judged stays judged), and
worker-node copies (core `foreign_ids`: their home board owns them). A `card` finding whose fingerprint already has
an open fix card (not done/archived/cancelled, any board: idempotency key in the range `session-repair:<fingerprint>:`,
any day, or a card the turns reported creating for that fingerprint, directly or through the intake) carries
`fix.open_fix_card`: comment there, never file a second card.

Rules for every fix:
- Re-read the card, producer and newer decisions: archived producers cannot deliver; answered asks need no duplicate wake. Report original-goal progress separately from chat activity or administrative repair—“running again” alone does not mean product progress.
- For primary/local-board custody refusals, follow [board-custody diagnosis](references/board-custody-diagnosis.md) before delegating.
- Use the Kanban tools/CLI only. Never edit a database, delete or archive a card, or kill/reclaim a live worker.
- A safe fix that did not take becomes a fix card, and counts against the card cap.
- A finding with `fix.open_fix_card`: ONE comment on that card (outcome `commented`), never a second card.
- Before any other fix card, look for open work on the same thing (the finding's `idempotency_key` prefix
  `session-repair:<fingerprint>:`, the card id, the pattern id, the subsystem, on every board): open = ONE comment,
  never a second card. The card body names the open or recently decided work on that subsystem and why none
  replaces it, plus evidence and acceptance (the detection no longer matches that card, plus "Done when").
  `idempotency_key` = the finding's (`session-repair:<fingerprint>:<UTC YYYYMMDD>`: a recurrence on a later day is
  a new card, never absorbed into a finished one). Linked to the repair chat. No model pin.
- `area: card`: the fix card is product work for the card's owner; never `self_work`.
- `area: hermes`: Hermes itself is the exception. An open card for that Hermes area gets one comment. With none, a
  new card only on a dispatcher, credential, quota/pacing or money path, or when the defect blocks the finding's
  card now; otherwise leave it (outcome `deferred`, with the reason). A new Hermes card carries
  `self_work={version:1, repository:'hermes-mods', area:'session-repair', kind:'sweep'}` only when the
  `kanban_create` schema exposes it. A queued acknowledgment is not a card: keep its request identity (outcome
  `queued`), never retry it undeclared or replace it.
- A human-only step is a blocked card with a valid `human_request`, only after try-to-unblock. It counts as a card.
- The cron hands at most 5 card findings per run (20 per 24 h, counting every card the turn reports) and 20 safe
  fixes per run; the turn's `session-repair-result` block decides what is marked seen: `fixed`, `card`, `queued`,
  `commented`, `moved_on`, `false_positive` and `deferred` with a `reason` are seen for 24 h; `over_budget` comes back
  next run, but only when that turn created its whole card budget (else it is a failed attempt). The block's `card`
  is only a card the turn created (or a queued request id); a comment goes in `commented_on`; an unknown outcome, a reasonless `deferred` or a finding left out is a failed attempt (backoff, given up
  after 5 with a `gave_up` ledger row).

## BS-ready-worker-free: ready, unclaimed, while a worker is free

**Seen as:** the panel's Ready section, `ready · waiting for a worker to claim it`, for a long time while the hub
or a pool worker is idle.

**Detection** (`rule_ready_worker_free`): status `ready`; ready since `ready_since`, else the newest
promoted/unblocked/woken/created event. Any dispatcher skip recorded in the current ready episode (core
`kanban_dispatch_skip.latest`, the queue view's `skip`, however old) explains the wait (no finding),
unless it is a timed hold whose `until` has passed. Otherwise, by core's queue view (`kanban_queue_position.read`):
- `lane: leftover_claim` (ready but still holding a claim: the dispatcher never picks it), ready > 10 min →
  `leftover_claim`;
- `pool_could_run` names pool workers, ready > 10 min → `pool_could_run`;
- `lane: local` and core's `kanban_dispatch_skip.unrecorded()` (ready >= 30 min, `running < limit`, no recorded
  skip) → `free_slot`;
- `lane: machine`, ready > 10 min (the skip check reads core's `latest()` directly: the machine-lane queue view carries no `skip`): a fitting machine that is active, unheld, not draining, seen within 5 min, with
  `max_card_runs - active_card_runs` > the cards ranked ahead on its labels → `free_worker`. Free slots are
  allotted oldest waiter first, so 15 ready cards and 3 free slots flag 3 cards, not 15.

**Evidence:** `queue_note`, running/limit, free machines and slots, pool workers, `ready_for_s`.

**Safe automatic fix** (`free_slot` only): `hermes kanban [--board B] dispatch --max 1 --json`, then re-read.
**Fix card** (area hermes): `leftover_claim`: clear the claim; `pool_could_run`: re-place the card on the pool
(`hermes kanban move`) or fix why it is held home-only; `free_worker`: why the worker does not lease it (its pickup
skips via `kanban_show` on that worker, skill hermes-workers). **Done when:** the card is claimed, or its queue
note names a real reason (pacing, quota, placement).

**Hand fix for a long ready wait:** `references/ready-wait-verification.md` (stale hub view first, then why
nothing claims it, whether it is still wanted, re-placement, foreign cards).

## BS-overdue-wake: a park whose wake never fired

**Seen as:** `⚠ 4h overdue · due Fri 13:50`, or `no wake published`, on a scheduled card.

**Detection** (`rule_overdue_wake`): status `scheduled`, and by core's `kanban_continuation.park_state`:
- a timed park whose `wake_at` is > 30 min past → `clock_overdue`;
- an untimed park with a wake probe (`task_wake_checks`, purpose `wake`) whose `next_at` is > 30 min past →
  `probe_overdue`;
- an untimed park with no probe, parked > 30 min → `no_wake` (core's invariant should have given it an implied
  wake or moved it to blocked).

**Evidence:** `wake_at`/`late_s`, the probe command, last rc and fails, the park reason.

**Safe automatic fix** (`clock_overdue` only): `hermes kanban [--board B] unblock <id>` (scheduled → ready, or todo
while a parent is open). **Fix card:** `probe_overdue` (area hermes): run the probe command
(`evidence.probe_cmd`, the first of `commands`) by hand first; exit 0 = its condition holds, then unblock; otherwise
leave the park and fix the stuck probe runner. A probe that runs on time but can never pass because its premise is
moot (e.g. a `capability-owner ... ssh:<host>` wait on a card whose home gateway is that host) is not overdue: read
the park reason, then unblock on the card's home host (`tailnet_exec`). There, `hermes kanban unblock <id> --seen
<newest comment id>` is the release; it refuses without `--seen` and prints the id to pass. `hermes kanban resume`
only releases `active_pr` holds. `no_wake` (area card): the owner re-parks with a real wake
(`kanban_schedule` with `awaiting`/`wake_at`/`wake_check`) or unblocks. **Done when:** the card left `scheduled`,
or holds a future wake.

## BS-unexplained-wait: a block nobody can act on

**Seen as:** `blocked · no reason given` (yellow), a red card with no ask in Needs you, or `stalled` on a
waiting card.

**Detection** (`rule_unexplained_wait`), status `blocked` or `triage`, never a card on a valid ask
(`needs_human` true: that is the user's move). Exclude canonical pending spend decisions in triage
(`kanban_spend_decision.pending`), including an owner-retained HOLD: never treat these as generic unblock work
or clear inherited `block_kind`. Core's projected `wait.waiting_since` can reuse an earlier scheduled episode
because its generic park vocabulary omits spend triage. Check the latest run admission/end and current decision
before trusting an age; the scanner fences ages at that run boundary and discards a generic park before admission.
The reason is the current episode's newest park event's (core
`kanban_wait_info.PARK_EVENT_KINDS`) `reason`, else its `error`:
- a `human_request` core's `needs_human` rejects → `invalid_human_request` (the ask is invisible);
- `blocked`, no `block_kind`, no reason, for >= 30 min → `no_reason`;
- an agent-owned wait (the session-tasks row's `wait.agent_owned`, else a block kind in core
  `kanban_wait_info.AGENT_BLOCK_KINDS`) older than 2 h → `agent_owned_stalled`, unless chat-closeout's owner wake
  already handled that card and wait premise (its `owner-wake.db` `fired` row: `started` or `routed` in the last
  24 h, or a claim with no result younger than chat-closeout's `CLAIM_TTL_S`; `too_old`, `error`, `refused`, `busy`
  and abandoned claims do not count): that chat was woken for exactly this.

**Fix card** (area card): `invalid_human_request`: re-file the ask validly (action, why_human, interaction, scope,
evidence) or clear the block if no person is needed (try-to-unblock). `no_reason`: ask the owning chat; unblock or
block again with a typed kind and reason. `agent_owned_stalled`: an agent clears the block. **Done when:** the card
is unblocked, or blocked on a valid ask.

## BS-parent-done: still waiting on something that finished

**Seen as:** a chain ending on a green `finished` leaf; `on <card> · done` for a long time.

**Detection** (`rule_parent_done`):
- `todo` (or `blocked` kind `dependency`) whose parents by core's `kanban_db.parent_states` (local, or the remote
  parent mirror) are all done or archived (cancelled does not satisfy, as in core's `recompute_ready`), the last
  one > 10 min ago, and none of core's deliberate holds: containment (`kanban_containment_connections.held`), a
  pending spend decision, or `recovery_held` (sticky stop, failure breaker at `kanban.failure_limit`) →
  `parents_done`;
- `scheduled` whose newest park's `awaiting` is a card (`kind: card`) that is done or archived > 10 min →
  `awaited_done`.

**Safe automatic fix** (`awaited_done` only): `hermes kanban [--board B] unblock <id>`.
**Fix card** (`parents_done`, area hermes): core promotes such a card on its own, so a residual one is a
promotion defect: find why `recompute_ready` skipped it; promote by hand only with that cause named. **Done when:**
the card is ready or running.

## BS-stuck-run: running, but nothing is running it

**Seen as:** Stuck section, `quiet N min`, a running card with no worker.

**Detection** (`rule_stuck_run`), status `running`:
- local claim expired > 5 min (`claim_expires`) → `claim_expired`;
- Board lease expired > 5 min (`lease_expires`, ms) → `lease_expired`;
- no claim, no lease, no current run → `no_claim`;
- no event of any kind (heartbeats count) for > 60 min → `quiet`.

**Fix card** (area hermes; never automatic: reclaiming a live worker's card loses its work): read
`hermes kanban log <id>` and `runs <id>`, check the lease holder (skill hermes-workers); reclaim
(`hermes kanban reclaim <id>`) only when the worker is gone (remote holder: `references/stuck-run-reclaim.md`).
**Done when:** the card has a live run with fresh events, or is back in ready.

## BS-review-no-reviewer: a review nobody runs

**Seen as:** `no reviewer running · review_requested 2h ago`.

**Detection** (`rule_review_no_reviewer`): status `review`, no current run in `running`, newest
review_requested / hub_review_admitted / review_route_deferred / status event > 60 min old. Review dispatch on
(`kanban.review_dispatch`) → `idle` (area hermes); off → `idle_manual_review` (area card).

**Safe automatic fix** (`idle`): `hermes kanban dispatch --max 1 --json`. **Fix card** (`idle_manual_review`):
start a review or send it back. **Done when:** a review run is running or the card left review.

## BS-broken-chain: waiting on nothing

**Seen as:** a chain ending on a dashed `unknown` leaf: `card could not be read`, or a cycle.

**Detection** (`rule_broken_chain`), any open, non-running card:
- a parent link to a card no live board, replica or remote-parent row has → `missing_parent`;
- a park awaiting a card (no `gateway` named) that no board here has → `missing_awaited`;
- a dependency cycle over parent links → `cycle`.

**Fix card** (area card): relink to the right parent or unlink the missing one
(`hermes kanban unlink <parent> <child>`): the owner decides, it changes what the card waits for. **Done when:**
every parent resolves and no cycle remains.

## BS-notice-undelivered: a notice that never woke its chat

**Seen as:** a chat whose last message is a Kanban notice with no reply; the sidebar shows it unresolved.

**Detection** (`notice_findings`, `subscription_findings`), unarchived, unmuted chats active in the last 3 days
(core `notice_guarantee.LOOKBACK_S`), never the Session repair chat:
- the chat's last active message is a `kanban_notice` whose close-out is final `error`, final `busy` (with
  `busy_attempts`), `deferred` > 2 h past `retry_at`, `claimed` > 2 h with no verdict, or unclaimed > 2 h (and
  not `blocked_on_human`, and naming no card blocked on a valid ask: core never wakes those by design) →
  `closeout_error` / `closeout_busy` / `deferred_overdue` / `claim_stuck` / `never_claimed`;
- a `tui` subscription whose oldest undelivered notify-kind event is > 60 min old (no inflight claim), for a
  live chat → `subscription_lag` (the unloaded sweeper never wrote the row).

**Safe automatic fix** (chat variants, area hermes): one close-out turn,
`hermes chat --resume <session> --oneshot -Q -q "[Kanban close-out] The Kanban notice above reached this chat and
no turn followed it. Act on it now."` (the turn core's guarantee would start). **Fix card** (`subscription_lag`,
or a close-out that fails again): find why the sweeper skips it (gateway log `kanban-unloaded`, `poller miss`).
**Done when:** a turn followed the notice / the cursor passed the event.

## BS-long-wait: potentially stuck on an external producer

**Seen as:** a correct CI, review, deployment, resource or card park that has lasted too long, or whose event
already happened. Unlike a broken wake, this asks the owner to reassess; it never releases a decision hold.

**Detection** (`rule_long_wait`): open `scheduled`/`blocked`/`todo` cards, no live run, never a valid human ask,
worker copy, repair-created card, containment or recovery hold. Core's newest `PARK_EVENT_KINDS` event, fenced by
latest run admission, supplies `awaiting` through `kanban_wait_info.parse_awaiting` and the episode's age.
When a legacy park has no `awaiting`, use core's derived wait plus `producer_from_wake_check`: a probe's
future `wake_at` is only a backstop, not a clock wait. An untyped probe is external (3 h); derived wait/reason
text naming deploy or CI selects 45 min. No probe and no producer remains excluded, as does an explicitly
declared clock. Projected `waiting_since` is fenced at the newest current-episode park; scan, prewrite and
transactional wake use the same producer resolver.
Thresholds (`LONG_WAIT_S`): CI and deploy **45 min**, external/review **3 h**, card **4 h** only when the producer
has had no event for **1 h**, resource **2 h**. Clock: never (`BS-overdue-wake` owns it). Canonical GitHub run/PR
URLs get a bounded `gh` read through token-broker: a completed run, merged/closed/approved PR or nonempty all-green
checks gives `event_happened` immediately, regardless of age. Otherwise `too_long` at the threshold. Failed reads
retain `readback_failed` evidence and still use age. Unreadable card-producer freshness fails closed. A matching
chat-closeout `owner-wake.db` fired premise is excluded, as for `BS-unexplained-wait`.

**Evidence:** kind, ref, waited_s, threshold_s, live_producer, park id/time, last card event.
**Safe automatic fix** (`nudge`, area card): re-read first; a moved park/event, human ask or running owner means no
write. Add ONE `[session-repair]` comment asking whether it is stuck, showing duration/limit/live state, and
asking it to continue, re-park with a fresh reason/wake, or block with the real reason. A comment alone does not
wake scheduled cards: issue core's supported `unblock_task` with transactional eligibility checks, preserving
parent gating. Never wake a clock, valid human ask, live run, containment, spend/safety or recovery hold. Record
the exact `unblocked` event carrying `session_repair_nudge=<comment id>`; a replay reads that same event and does
not wake twice. Keep the complete comment under **1,000 characters**, with one plain live-state line, not raw JSON.
**Done when:** an owner run starts after this recorded wake and reassesses its wait; a card's independent
`condition_met` probe is not nudge-caused proof.

## BS-quiet-chat: unresolved cardless chat with no executor

**Detection** (`quiet_chat_findings`): unarchived/unmuted desktop/tui chats active in the last **3 days**, stored
`hermes sessions outcome-audit --json` outcome `unresolved` or `waiting`, last activity more than **60 min** ago.
No open linked card (including compression lineage), owned live cron, running turn/process/subagent or open ask.
The Session repair chat is always excluded, by id and title. Unreadable guard inputs fail closed with a scan error.

**Safe automatic fix** (`nudge`, area card): re-read eligibility and last message; one turn through
`hermes chat --resume <session> --oneshot -Q -q "[session-repair] ... Are you stuck? ..."`.
**Done when:** the chat takes a turn, resumes work or names its real wait.

### Nudge cadence and rollout (both rules)

`config.json` **nudge_mode** is `shadow` (default) or `live`, independently of the existing fixes' `mode`;
`mode=off` remains a global stop. Re-nudge the same fingerprint every **2 h**, not the other rules' 24 h seen
window. Second and later messages include count and earlier UTC times. Three unanswered nudges (no subsequent
owner activity) produce one normal area-card repair finding, never a fourth nudge. Only owner-run events,
owner-authored comments, status changes and re-parks reset the unanswered sequence. `external_watch`,
`wake_check_alive`, `wake_probe_unproven_notice` and the nudge's own comment/wake never count as answers.
Keep the send times/cooldown when migrating an older probe-inclusive checkpoint.
Nudge limits: **15/run**, **100/rolling 24 h**, separate from fix-card caps.
Durable write-ahead pending delivery prevents crash/timeout resend; exact comment/message readback is the receipt,
and unknown delivery stays pending for recovery, not a blind retry. `nudges-<mode>.json[l]` holds cadence/receipts;
shadow rows also go to `actions-shadow.jsonl`. Never copy live chat/card evidence into the public repository.

Install merged code on the hub in shadow; adjudicate every would-nudge row from **two consecutive 15-minute
ticks** (about 30 min), preserving rows and verdicts on the owning card. Then set only `nudge_mode=live`; verify an
exact comment/chat receipt and a following owner event. This shorter nudge-only gate does **not** waive the existing
24-hour shadow gate for the other fixes. To reverse rollout, set `nudge_mode=shadow` without changing `mode`.

## Adding a pattern or example

When the user shows a broken session (screenshot, chat id, "this one again"):
1. Run the scanner for that chat's cards. Collect: session id, card ids and boards, `kanban_show` of each card on
   the chain, what the panel showed, what fixed it.
2. Save the example **privately**: `~/.hermes/skills/devops/broken-sessions-examples/<id>/<YYYY-MM-DD>-<slug>.md`
   (session id, card ids, what was seen, the finding or "no rule caught it", what fixed it; its README has the
   template). `<id>` is the pattern id, or `new` when none fits. Personal data (chat titles, card bodies, names)
   never goes in hermes-mods: that repo is public. Not `~/.hermes/skills/devops/broken-sessions/`: hermes-sync
   moves any local copy at a hermes-mods skill's path aside (`mods_link`), examples included.
3. Turn it into a synthetic fixture: a builder in `plugins/session-repair/tests/fixtures/patterns.py` that
   recreates the state with core's `kanban_db` and generic titles, plus a positive and a negative test in
   `tests/test_patterns.py`.
4. If no rule caught it: add a rule to `plugins/session-repair/scan.py` (`rule_<name>` → id `BS-<name>`, append
   to `RULES` and `CARD_RULES`) and a section here with the same id: seen as, detection (data and thresholds),
   evidence, safe automatic fix (only if reversible and already a Kanban CLI/tool action), fix card with its
   `area`, done when. Reuse core's own predicates and constants (never a copy) and honour core's deliberate holds.
5. A rule that misfired (the repair chat called it a false positive): tighten the rule, add the counter-example as
   a negative test.
6. Run the suite (`plugins/session-repair/README.md`, Tests), commit in a hermes-mods worktree.

The scanner's own thresholds are constants at the top of `scan.py`; this file quotes them. Change both together.
