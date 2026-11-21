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

A chat is "broken" when one of its Kanban cards (or a card on its dependency chain) sits in a state nothing will
move it out of: a ready card no worker takes although one is free, a wake that never fired, a block nobody can act
on, a run whose worker is gone. The Desktop session panel shows these as odd waits; the user then fixes them by
hand. This skill is the catalog of those patterns. Each has a stable id (`BS-...`), and the `session-repair`
plugin (`plugins/session-repair` in hermes-mods) implements each id as a rule with the same detection: its
scanner runs every 15 min on the hub and hands new findings to the chat titled **Session repair**.

Use it two ways:
- **Diagnose one chat** the user points at: run the scanner (below), read the finding's evidence, apply the
  section's fix. No finding but the chat is clearly stuck? Collect the evidence and add a pattern (last section).
- **Act on the repair chat's findings**: each finding names its section; follow "Safe automatic fix" or "Fix card".

```sh
# read-only; on the hub (where the boards live). Prints {findings: [...], errors: [...]}
nice python3 ~/.hermes/plugins/session-repair/scan.py              # every chat with an open linked card
nice python3 ~/.hermes/plugins/session-repair/scan.py --rule BS-overdue-wake
curl -s localhost:<dashboard port>/api/plugins/session-repair/findings?chat=<session id>   # last cron scan
```

Every finding carries `id`, `variant`, `card`, `board`, `chat`, `evidence`, `fix {kind, action, commands, why}`
and `fingerprint` (`<id>:<board>/<card>`, or `<id>:chat/<session>`). Fix kinds: `auto` = safe and reversible, the
repair chat applies it; `card` = needs real work, file a fix card; `human` = only a person can decide.

Rules for every fix:
- Re-read the card first (`kanban_show <id>`); findings can be 15 minutes old. Moved on = do nothing.
- Use the Kanban tools/CLI only. Never edit a database, delete or archive a card, or kill/reclaim a live worker.
- A fix card: one per finding, `idempotency_key='session-repair:<fingerprint>'`, linked to the chat it is filed
  from, acceptance = the pattern's detection no longer matches that card plus the section's "Done when";
  `self_work={version:1, repository:'hermes-mods', area:'session-repair', kind:'sweep'}` when the `kanban_create`
  schema exposes it. No model pin. The cron caps hand-overs at 5 card-needing findings per run, 20 per day.
- A human-only step is a blocked card with a valid `human_request`, and only after try-to-unblock.

## BS-ready-worker-free: ready, unclaimed, while a worker is free

**Seen as:** the panel's Ready section, `ready · waiting for a worker to claim it`, for a long time while the hub
or a pool worker is idle.

**Detection** (`rule_ready_worker_free`): status `ready`, ready for > 10 min (`ready_since`, else the newest
promoted/unblocked/woken/created event), no dispatcher skip recorded in the last 15 min (a recent skip explains
the wait), and core's queue view (`kanban_queue_position.read`) says a slot is free:
- `lane: local` and `running < limit` (host workers vs `kanban.max_in_progress`) → variant `free_slot`;
- `pool_could_run` names pool workers → `free_worker`;
- `lane: machine`: a fitting machine that is active, unheld, not draining, seen within 5 min, with
  `max_card_runs - active_card_runs` > the cards ranked ahead on its labels → `free_worker`. Free slots are
  allotted oldest waiter first, so 15 ready cards and 3 free slots flag 3 cards, not 15;
- `lane: leftover_claim` (ready but still holding a claim: the dispatcher never picks it) → `leftover_claim`.

**Evidence:** `queue_note`, running/limit, free machines and slots, `ready_for_s`, the last skip.

**Safe automatic fix** (`free_slot` only): `hermes kanban [--board B] dispatch --max 1 --json`, then re-read.
**Fix card** (`free_worker`, `leftover_claim`, or the dispatch did not claim it): why the worker does not lease it
(its pickup skips via `kanban_show` on that worker, skill hermes-workers), fix that or re-place the card.
**Done when:** the card is claimed, or its queue note names a real reason (pacing, quota, placement).

## BS-overdue-wake: a park whose wake never fired

**Seen as:** `⚠ 4h overdue · due Fri 13:50`, or `no wake published`, on a scheduled card.

**Detection** (`rule_overdue_wake`): status `scheduled`, not a worker node's copy (`foreign_ids`), and by core's
`kanban_continuation.park_state`:
- a timed park whose `wake_at` is > 30 min past → `clock_overdue`;
- an untimed park with a wake probe (`task_wake_checks`, purpose `wake`) whose `next_at` is > 30 min past →
  `probe_overdue`;
- an untimed park with no probe, parked > 30 min → `no_wake` (core's invariant should have given it an implied
  wake or moved it to blocked).

**Evidence:** `wake_at`/`late_s`, the probe command, last rc and fails, the park reason.

**Safe automatic fix** (`clock_overdue`; `probe_overdue` after running the probe by hand and seeing its
condition met): `hermes kanban [--board B] unblock <id>` (scheduled → ready, or todo while a parent is open).
**Fix card** (`no_wake`, or a probe that is not met): the owner re-parks with a real wake (`kanban_schedule` with
`awaiting`/`wake_at`/`wake_check`) or unblocks. A dispatcher that wakes nothing for 30 min is a gateway defect: say
so in the card. **Done when:** the card left `scheduled`, or holds a future wake.

## BS-unexplained-wait: a block nobody can act on

**Seen as:** `blocked · no reason given` (yellow), a red card with no ask in Needs you, or `stalled` on a
waiting card.

**Detection** (`rule_unexplained_wait`), status `blocked` or `triage`, never a card on a valid ask
(`needs_human` true: that is the user's move):
- a `human_request` core's `needs_human` rejects → `invalid_human_request` (the ask is invisible);
- `blocked`, no `block_kind`, empty reason on the newest park event, for > 10 min → `no_reason`;
- an agent-owned wait (the session-tasks row's `wait.agent_owned`, else block kind transient / capability /
  assignment / needs_input) older than 2 h → `agent_owned_stalled`.

**Fix card:** `invalid_human_request`: re-file the ask validly (action, why_human, interaction, scope, evidence)
or clear the block if no person is needed (try-to-unblock). `no_reason`: ask the owning chat; unblock or block
again with a typed kind and reason. `agent_owned_stalled`: an agent clears the block. **Done when:** the card is
unblocked, or blocked on a valid ask.

## BS-parent-done: still waiting on something that finished

**Seen as:** a chain ending on a green `finished` leaf; `on <card> · done` for a long time.

**Detection** (`rule_parent_done`):
- `todo` (or `blocked` kind `dependency`) whose parents (`task_links`, local or `kanban_sync_remote_parents`)
  are all done/archived/cancelled, the last one > 10 min ago → `parents_done`;
- `scheduled` whose newest park's `awaiting` is a card (`kind: card`) that is finished > 10 min → `awaited_done`.

**Safe automatic fix:** `parents_done`: `hermes kanban [--board B] promote <id>`; `awaited_done`:
`hermes kanban [--board B] unblock <id>`. **Done when:** the card is ready or running.

## BS-stuck-run: running, but nothing is running it

**Seen as:** Stuck section, `quiet N min`, a running card with no worker.

**Detection** (`rule_stuck_run`), status `running`:
- local claim expired > 5 min (`claim_expires`) → `claim_expired`;
- Board lease expired > 5 min (`lease_expires`, ms) → `lease_expired`;
- no claim, no lease, no current run → `no_claim`;
- no event of any kind (heartbeats count) for > 60 min → `quiet`.

**Fix card** (never automatic: reclaiming a live worker's card loses its work): read `hermes kanban log <id>` and
`runs <id>`, check the lease holder (skill hermes-workers); reclaim (`hermes kanban reclaim <id>`) only when the
worker is gone. **Done when:** the card has a live run with fresh events, or is back in ready.

## BS-review-no-reviewer: a review nobody runs

**Seen as:** `no reviewer running · review_requested 2h ago`.

**Detection** (`rule_review_no_reviewer`): status `review`, no current run in `running`, newest
review_requested / hub_review_admitted / review_route_deferred / status event > 60 min old.

**Safe automatic fix** (review dispatch on, `kanban.review_dispatch`): `hermes kanban dispatch --max 1 --json`.
**Fix card** otherwise: start a review or send it back. **Done when:** a review run is running or the card left review.

## BS-broken-chain: waiting on nothing

**Seen as:** a chain ending on a dashed `unknown` leaf: `card could not be read`, or a cycle.

**Detection** (`rule_broken_chain`), any open, non-running card:
- a parent link to a card no live board, replica or remote-parent row has → `missing_parent`;
- a park awaiting a card (no `gateway` named) that no board here has → `missing_awaited`;
- a dependency cycle over parent links → `cycle`.

**Fix card:** relink to the right parent or unlink the missing one (`hermes kanban unlink <parent> <child>`): the
owner decides, it changes what the card waits for. **Done when:** every parent resolves and no cycle remains.

## BS-notice-undelivered: a notice that never woke its chat

**Seen as:** a chat whose last message is a Kanban notice with no reply; the sidebar shows it unresolved.

**Detection** (`notice_findings`, `subscription_findings`), unarchived, unmuted chats active in the last 3 days
(core `notice_guarantee.LOOKBACK_S`):
- the chat's last active message is a `kanban_notice` whose close-out is final `error`, final `busy` (with
  `busy_attempts`), `deferred` > 2 h past `retry_at`, `claimed` > 2 h with no verdict, or unclaimed > 2 h (and
  not `blocked_on_human`, and naming no card blocked on a valid ask: core never wakes those by design) →
  `closeout_error` / `closeout_busy` / `deferred_overdue` / `claim_stuck` / `never_claimed`;
- a `tui` subscription whose oldest undelivered notify-kind event is > 60 min old (no inflight claim), for a
  live chat → `subscription_lag` (the unloaded sweeper never wrote the row).

**Safe automatic fix** (chat variants): one close-out turn,
`hermes chat --resume <session> --oneshot -Q -q "[Kanban close-out] The Kanban notice above reached this chat and
no turn followed it. Act on it now."` (the turn core's guarantee would start). **Fix card**
(`subscription_lag`, or a close-out that fails again): find why the sweeper skips it (gateway log
`kanban-unloaded`, `poller miss`). **Done when:** a turn followed the notice / the cursor passed the event.

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
   evidence, safe automatic fix (only if reversible and already a Kanban CLI/tool action), fix card, done when.
5. A rule that misfired (the repair chat called it a false positive): tighten the rule, add the counter-example as
   a negative test.
6. Run the suite (`plugins/session-repair/README.md`, Tests), commit in a hermes-mods worktree.

The scanner's own thresholds are constants at the top of `scan.py`; this file quotes them. Change both together.
