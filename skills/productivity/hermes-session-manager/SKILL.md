---
name: hermes-session-manager
description: "Use when asked what's blocked on me or to keep chats moving."
triggers:
  asks:
    - "what's blocked on me"
    - "archive finished sessions"
    - "keep an eye on my chats and cards"
  not_for: "Claude Code desktop session dispatch (use concierge)"
---

# Hermes session manager

Triggers: "manage my sessions", "what's blocked on me", "archive finished sessions", "make sure things are moving",
"keep an eye on my chats and cards"; every sweep wakeup of a session that already has this role. Claude Code desktop
sessions (`start_session`/`SendMessage`) use `concierge` instead.

Route quickly, recover stalled work, and verify closeout in the originating session.

## Per user message: route first

| Route | When | Do |
|---|---|---|
| **Answer here** | A few tool calls: status, "what's blocked", a lookup | Answer from the ledger and one fresh read |
| **Existing work** | A card or session already owns it | Comment on the card (`kanban_comment`) or resume that session with the message; reply with the id. A comment subscribes this chat (`via=subscribed`); that link doesn't hold the chat open for work, so bind it with `hermes kanban --board <b> notify-subscribe <task> --platform tui --chat-id <this session>` (`kanban_origin_adopt` is for cross-gateway courier adoption only) and read back `outcome` = `waiting` on that card |
| **New work** | Anything bigger or long-running | `kanban_create` a card with acceptance criteria (never `hermes kanban create` from a shell, so it links to this chat); reply with the card id at once |

Never wait on a worker inside a turn and never predict its result. For a user-requested separate chat that preserves stopped work without restarting it, follow [Preservation handoff](references/preservation-handoff.md).

## What to read on each sweep

1. **Sessions:** `hermes sessions outcome-audit --json` first (see Classify), then `hermes sessions list --limit 60` (add `--workspace <repo>` to narrow). For
   detail, query `~/.hermes/state.db` with `~/.hermes/bin/hermes-core-python -m sqlite3 ~/.hermes/state.db "<select>"`
   (the system `sqlite3` is refused on live DBs, WAL-reset bug): `sessions` has `title`,
   `source`, `parent_session_id`, `last_activity_at`, `last_activity_description`, `ended_at`,
   `end_reason`, `archived`, `pinned`, `hidden`. Use `session_search` to read what a session last
   said.
2. **Cards:** `hermes kanban list --status blocked`, `--status running`, `--status ready`,
   `--status review` (and `hermes kanban boards list` for other boards). `hermes kanban show <id>`
   for one card.
3. **Inbox:** `hermes inbox list` (open proposals waiting on the user).

## Classify every live item

**Outcome first.** Read each chat's stored outcome before anything else:
`hermes sessions outcome-audit --json` (every sidebar row as `{id, title, kind, reason, age_h, on, items}`), or
`hermes sessions outcome <id> --json` for one. Map it directly: `decision` → blocked on the user (its `items`
are the asks), `waiting`/`running` → inspect the named work, `done` → done (`summary`), `muted` → leave
alone, `unresolved` → stalled, with `reason`. Stored waiting/running is not execution evidence. After recovery
or ownership changes, refresh this authoritative outcome; an immediate declared-done readback can precede
link derivation and revert to waiting. Use the fallback rules below only where no authoritative outcome exists.

- **Blocked on the user:** a blocked card whose blocker is the user, an open Inbox item, or a
  session whose last assistant message asks the user something (a question, an approval, a click,
  a password). Collect the exact ask.
- **Stalled:** running with no activity for 30+ min, a card `running` with a stale heartbeat, a
  `ready` card nobody claims, a session that keeps failing the same way, an update or drain waiting
  on a turn that is not really running. Investigate before declaring it stuck.
- **Moving:** recent activity and no ask.
- **Done:** card `done`, or a session whose last message reports the work finished (shipped,
  merged, verified) with nothing pending.

**Never report stale state.** The user also talks to sessions and cards directly. Before listing
anything as blocked on the user, re-read that card or session live in the same turn and confirm
the ask is still open. If you can't confirm it, mark it "unverified". The ledger and status file
say what's running, never what needs the user.

## Make things move

- Stalled card: read its log (`hermes kanban log <id>`), then `kanban_comment` with the next step,
  `reclaim` a dead claim, or unblock it if the blocker is gone. A comment does not wake a `scheduled` card: follow
  it with `kanban_unblock(task_id, seen_comment_id=<that comment>)` and confirm a run starts. Fix it yourself when it is small.
  When the user asks whether *their* cards run, count `running` rows in `hermes kanban chat-tasks <chat>`, never
  the board-wide `--status running` list: a full global cap is often all Hermes self-work, so "N running" from it
  is a wrong answer.
  Before explaining a `ready` card as "waiting for a slot", read the live capacity. Compare the latest
  `kanban dispatcher: max_in_progress=N` line in `~/.hermes/logs/gateway.log` against running counts
  summed across every board DB. Also grep that log for `dispatch held for a core update drain`, or check
  for `~/.hermes/core-update/drain-hold`. Remembered capacity goes stale, and a drain holds every spawn
  however many slots are free. The latest `dispatcher stuck` line names each held card's reason
  (`update_admission_hold`, `labels_mismatch`, `waiting_for_worker_capacity`). For a pool card, read that line on the
  workers (`~/.hermes/bin/hub-worker-exec all -- '<cmd>'`; one target or `all`, not a list): `claim_deferred(sync_first_round)`
  on every card means that worker's sync loop is dead and it claims nothing. Confirm with `hermes kanban sync status`
  (`running: no`) and `grep "kanban_sync_watcher died" gateway.log`. That is a pool outage: no bump or re-place helps, so
  file the crash on its own card. When you bump a card the chat
  waits on, first read the priority spread across `ready`, `scheduled` and `todo`, plus the priorities of
  recent `spawned` events (`task_events` joined to `tasks`). Set it above the top of all of them, because
  scheduled cards and continuations that wake later, at 9000+, jump ahead of a value that only beat the ready
  queue. The notices' suggested `--priority 10` does nothing in this case. A card created on another gateway but
  pinned here fails `hermes kanban edit` with "unknown id" (`kanban show` says "not imported here; last pickup
  skip: budget_exhausted"). Pickup imports only one foreign card per free slot, in effective-priority order, and
  waiters donate their priority. So first raise its local waiting child, and once the card is imported, set the
  card itself, because older cards boosted by their own waiters can still spawn ahead of it. When the hub is
  full and the work is portable (code, tests, PR, CI), skip the bump and re-place it onto the whole pool:
  `hermes kanban move <id> --pool --reason "..."` (= `--runs-on pool:worker`, the Board label of pool workers).
  Never `machine:worker-N`,
  which waits again when that worker fills or is held, unless the card holds unpushed state or its ship's review
  card on that worker (then `--runs-on machine:worker-N --host-reason "..."`). Add a comment that a hub-only readback
  becomes a small hub-pinned card, never a move back. Verify a claimed run and
  attributable worker activity; `~0 ahead` is queue position, not execution. If a repeat notice arrives
  without execution, dispatch an independent reviewer directly or re-place the existing work rather than
  repeating priority-only updates. Have the reviewer check for a dispatcher claim before running tests:
  if that worker has started, coordinate through the card and distinguish source-only findings from the
  test-backed verdict so concurrent recovery neither duplicates tests nor bypasses the review gate.
- A chat that went silent after the user's message with no reply: grep `agent.log*` for
  `prompt.submit for session <ui_session> queued behind the core-update admission hold` with no later
  `tui prompt accepted` for that ui_session, plus `startup_resume: marker <session> ... skipped: writer pid N is
  still alive`. That's a prompt stranded in an old Desktop `serve` that outlived the update. Re-send the user's
  exact text with `cross_session.send` (it runs, unlike the stranded copy), then check for other chats stranded
  by the same hold. A later replacement of that `serve` replays the marker too, so check for double delivery.
- "I made a chat about X and can't find it": search user-role `messages` (not titles) in every gateway's
  `state.db`: hub `~/.hermes/state.db` plus `profiles/*/state.db`, then MacBook and mini via `tailnet_exec`
  (`mbp`, `mini`), last 2–4 days, archived/hidden included. Drop rows starting with `✔ ⏸ ⏳ ❓ [ #` (notices,
  compaction replays, card prompts) and dedupe on the first 60 chars, or Kanban traffic floods the output
  past 50k chars; then regex the topic words (and synonyms: "top bar" → strip/lane/pill/header). Also list
  desktop sessions with no user message (a send that never landed). A user's chat can also be an older chat where
  they asked a follow-up, so report near-matches by title. No match on any gateway = the prompt never left the app:
  say so plainly, point at the closest existing chat, and offer the work with a stated default.
- When a chain waits on a PR whose exact head is green and `CLEAN`, with no changes requested, but its owner card
  is queued, merge it yourself (`gh pr merge --rebase --match-head-commit <head>` via token-broker). Then
  `kanban_comment` each owner card that the merge step is done, so it resumes from the next step.
  A green PR left unmerged behind a queue is a stall, not progress. Hermes core repos (daveey-hermes) refuse
  a direct `gh pr merge` (ship-only-guard): land via `token-broker exec --scope user/github --reason "<why>" -- hermes-core-wt ship <slug> --review-task <card>`
  (no `--reason` = immediate refusal), which accepts only a done Kanban review card with verdict/reviewed_sha metadata.
  Never pipe a backgrounded ship/merge job through `| tail`: the pipe's exit 0 makes the completion notice say
  "completed normally" over a refusal; add `set -o pipefail` and read the output, not the exit line. A subagent's PR review comment does
  not satisfy it, so file that review card first, not after a subagent review.
- Never archive or complete a card you don't own because its checkpoint condition looks met (a wait for CI to
  settle, later green runs): first read its current body and newest comments, since the owner may have rescoped
  acceptance after the checkpoint; later green runs don't satisfy a "no unfinished runs" or "this failed run
  repaired" acceptance. A card you think is moot gets an evidence comment for its owner, not an archive. To undo
  a mistaken archive, the CLI can't (`reopen` takes only `done`), but the Dashboard API can: `GET
  /api/plugins/kanban/workflow` lists `archived -> ready/todo/triage`, then `PATCH
  /api/plugins/kanban/tasks/<id>?profile=<p>&board=<b>` with `{"status":"triage"}` restores the same card (body,
  parents, run allowance intact), so a restorable card needs no successor card.
- A `❓ parked until you decide` notice on another chat's card is that origin's decision: don't answer it, and
  before relaying it (or filing a relay follow-up card) read the card's comments newer than the ask, because the
  origin gets the same notice and usually answers first, and a relay of an answered ask reads as a fresh ask.
- David wants execution on workers and the hub kept for sessions. Before accepting "it runs on the hub", read
  the card's `requires` (a `host_repo` entry pins it to the dispatcher host), `placement_policy`/`runs_on`, and
  any `moved` events back to the hub. Give the concrete reason: an old card made before worker-first, a missing
  worker capability, or a check that really is hub-local. Then push the capability fix or split out the hub-only
  part. "It needs the hub" is a claim you must back with evidence. A `scheduled` card whose awaited review or parent just finished can be
  `kanban_unblock`ed at once instead of waiting out its wake interval. Before saying when a `scheduled` card
  wakes, read the `status:` wake line of `hermes kanban show` (`wakes when `<cmd>` passes …, backstop T`; `--json`
  `.wakes`, the kanban_show tool's `wakes`/`Wakes:`). Core builds before that line exists: read the `wake check:`
  field. Quote the backstop time only when no check is armed: a check on the producer wakes it sooner. Report
  "wakes when <producer> finishes" and name that producer's
  live heartbeat; don't quote the backstop time as the next step or add a redundant link that changes nothing.
- For a completion sweep ("get all sessions green"), inspect each remaining deliverable and why it stopped,
  then resolve authorized asks and resume concrete work. If a recipient only relays the instruction back,
  direct it to produce the missing artifact in its own session; a coordination reply is not execution.
  Reuse existing cards; preserve physical, privacy and spend gates.
- For David's soak-only waits, run the existing check immediately rather than wait out an observation window.
  Record his duration waiver on the card, report the actual window, and close on checked evidence—not a
  fabricated full-soak pass. Repair failing checks without another soak-only delay. Quota resets, unfinished
  implementation and third-party locks are not soaks; never shorten real data-retention periods to test expiry.
  Recheck follow-up children created by repairs: they can reintroduce six-hour or sample-count observation waits
  after the original duration waiver. Apply the same quoted duration-only rescope and verify real check output,
  not just wake delivery. Keep partial-window failure, absent-workload NO_PROOF, and actual repair execution
  separate; a child marked running needs attributable tool receipts before it counts as started recovery.
- Before undoing a model migration, recover the original user instructions from the tool that performed it:
  Hermes `state.db` does not contain Claude Code's `~/.claude/projects/*/*.jsonl` user messages. Read the
  timestamped user rows and distinguish existing-card migration from later new-card defaults; a commit message
  or an incomplete search cannot establish that prior pins should be restored.
- Verify execution after dispatch: `cross_session.send` can return `delivered=true, status=streaming`
  while agent initialization subsequently fails. Read back the exact message, then require a new tool
  operation or substantive conclusion; correlate a silent chat's runtime ID with `logs/errors.log`.
  For authorized session-only model recovery, use the owning gateway: `session.resume`, then
  `session.mutate` with `operation="model"`, a fresh `request_id`, the snapshot's `revision` as
  `expected_revision` and `execution_generation` as `expected_generation`. Set `payload={"model":"reset"}`
  to return to the profile default, or name the authorized model/provider. The legacy `config.set`
  model route can refuse with `use_session_mutation_model`; use the fenced mutation, not profile edits
  or SQLite writes. Resume again and verify `info.model`/`info.provider`. This proves the selection,
  not successful inference; require actual execution before claiming the chat runs again. Preserve
  independent-review identity gates.
- Finish in the originating session: a conclusion stating what was done, what was verified and
  why the work is finished, plus its done outcome. For obsolete work, state why it is no longer
  relevant instead of claiming implementation success. Archiving is not a substitute for this conclusion.
- For an owner-terminal blocker, read the existing handoff and consumed receipts first. A worker's
  identity refusal does not establish that the user must act: try the approved `run_in_user_terminal`
  flow from the owner-facing chat. If it succeeds, record the exact receipt, unblock the existing card,
  and verify resumed execution; never replay completed recovery steps or bypass the identity gate.
  Keep unfinished delivery open; delegated judgment still cannot supply credentials, physical evidence or extra spend.
- When writing an `action_request` for a blocked ask, re-read every fact you put in `limits` (quotas, accounts,
  regions, counts) live from the target. A worker treats an approved number as exact, so a stale figure copied
  from an older card body blocks it again on the mismatch. If ask-gate refuses it as `agent_can_do`, republish
  only when the scope opens by naming what is new (new exposure, risk or spend), not the steps.
  Before filing, check whether an existing approval already covers the step (its `outcomes`/`exclusions`: a
  reversible stop the approved plan implied is covered; only an explicit exclusion like termination or deletion
  needs a new ask) and whether the credential scope is auto-tier. If so, do it and skip the ask.
  `choices[].recommended` accepts only `true`: put it on the choice that is the safe default (decline/keep for
  irreversible deletes), and omit the field on the others.
- "Spend budget decision: <title>" cards (body "Card X has stopped. No chat owns its decision") are administrative wrappers. The system raises them as asks to David, but they are not his decisions.
  1. Read the source card's stop reason and the cards it names.
  2. If the premise is gone (the named cards moved on, or the source says its premises are superseded), comment that on the source, then archive both the source and the wrapper.
  3. Otherwise comment the disposition on the source and on the wrapper (held, no new spend; supervisor should retain), and leave the wrapper open. Archiving only the wrapper re-files a fresh one for the same stop about 2h later ("Decide this parked card … decision_retained"), because an archived wrapper counts as no owner. Only archiving the source ends the cycle.
  4. Check `task_links` for children first, then withdraw the ask with `action_request`. A withdraw that returns 409 "A decision is already recorded" means David already clicked a choice that never reached you: find it (the undelivered-decision incident card names the operation), and deliver it before anything else. For Unblock, comment the choice on the wrapper, then `promote` it.
  5. If David asks what one means, explain the source work in plain words: what it builds, why it stopped, what fixing it would take.
  Don't relay the wrapper's "Unblock = promote" line. A source held by a safety refusal ignores `promote`, which prints success without applying it.
- A scope-stopped card owned by another chat: that owner often posts its renewal mandate comment minutes before (or without) running `renew-scope` and `promote`.
  - After the mandate comment, read `task_events` for `scope_renewed` or `promoted`; a mandate comment alone releases nothing.
  - If neither has run, or `cross_session` to the owner is refused ("active in another window"), file one 1-run pool card. It re-checks the card, and only if the card is still stopped posts a single hub comment to the owner naming the exact `renew-scope --episode <uid> --mandate-comment <id>` command, then reads it back. Owners answer such comments, so no second card or chat relay is needed.
- When a worker blocks on a conflict between the approval's literal limits and live facts, settle it yourself:
  pick the reading that satisfies both the intent and the letter, and leave anything outside the approved scope
  untouched as a named follow-up. Then `kanban_comment` the ruling and unblock, rather than asking the user again.
- A `decision` outcome whose `items` name a card this chat doesn't own: check `hermes kanban chat-tasks <chat> --json` for that card's `via`. `subscribed` means a `kanban_comment` here imported the foreign card's `human_request`, which is often a system parking stamp, as this chat's ask. Linking the card under your own card or `kanban schedule` doesn't clear it. Run `hermes kanban notify-unsubscribe <task> --platform <from notify-list> --chat-id <chat>`, then `outcome --refresh`. Before commenting on another chat's parked card, expect this and unsubscribe afterwards.
- "Why are you red?" (asked in the red chat itself): read the recorded reason, never guess from linked cards:
  `hermes sessions outcome $HERMES_SESSION_ID` prints a `Desktop stripe <x> ...` line with the stuck inputs the
  app reported (needs-input + runtime, stalled, kanban_blocked + card ids, review_wait, outcome decision); the raw
  history is `<home>/logs/desktop-stripe.jsonl`. Needs core 53638e3f23 on the gateway and the Mac's Desktop; no
  line means an older build or no red since it started.
- "Still red" / "no green bar" is about the Desktop sidebar stripe, not the stored outcome. The app can paint a row
  red on its own (a queue card a restart lost, an open prompt, a turn gone quiet) while `outcome --refresh` reads
  `waiting` or `done`. Never tell David a chat is green or isn't red from `outcome` alone: read the row yourself on the Mac he
  uses (inspecting-hermes-desktop-dom `rend-eval.mjs`: the row's `[data-slot=session-status-stripe]`
  `data-status-stripe` and `aria-label`) before asking him. A restart-lost queue card reads "A message queued
  before a restart may not have run: Send again or Discard" (either clears it). A red row with an empty tooltip
  whose `outcome --refresh` is `decision` is still that ask: lead the reply with its `items` question and choices,
  and say it is answered in the chat's "This session → Needs you" panel above the composer. A `done` outcome with a non-empty
  `crons` list (the chat is `owner_session_id` of a live job in `cron/jobs.json`) draws a green loop glyph, never
  the bar, so check `crons` in `outcome --refresh --json` before promising green. That owner field only feeds the
  sidebar; releasing it doesn't change how the job runs.
- "Why was it stuck grey?": read `desktop-stripe.jsonl` for the row's last change. A chat whose work ended but
  that never called `session_outcome` stays derived `waiting`/`running` for days, held by a linked card parked
  far out (`scheduled` to a future date) or a cron it owns. Every chat you work in: when its work ends, call
  `session_outcome` that turn (`done` + `keep_crons=false`, or `wait` naming the exact card/cron/time), and
  unsubscribe it from cards you park for days. "Your request was not processed" with no reply: grep the logs
  for `agent build failed for session <ui> (<session id>): SubscriptionQuotaUnavailable` (route out of quota,
  turn never started) before suspecting the chat. Waiting though every listed card is done: run
  `hermes kanban chat-tasks <chat> --json` (a bare list of rows) and keep the `unfinished: true` rows, the ones
  holding the chat open (a watch park reads false); the ask-gate list and plain `kanban show` cover only the default
  board (`no such task` = use `--board <b> show`). A `done_pending_release` row (`released: false`) is a finished
  card whose `task_chat_links` row on its home board still reads `released=0`: that is link-release machinery, so
  record it as a HERMES-NOTE on the open card for that area. A coach card filed with
  this chat as its origin (`via=created`, empty `notify-list`) can't be detached (`edit --session-id` refuses
  empty); if it is lane-gated with no runner, move the defect to a HERMES-NOTE comment and archive it with
  restore steps.
- Verify completion with `hermes sessions outcome <id> --refresh --json`, then read it back; without
  `--refresh` the command only reads stored state. Live card links can restore `waiting` after a done declaration. Trace remote links to the
  owner's `kanban notify-list <task> --json` and the card's named canonical origin. Remove only a demonstrably
  obsolete observer via supported `notify-unsubscribe`, sync owner and consumer, then re-read the derived
  outcome; preserve canonical ownership and unmet acceptance. If a child-context mutation fence refuses the
  cleanup and no corresponding tool exists, hand the exact operation and evidence to the parent; never route
  around the fence or edit the subscription database.

## Done means
- Did you re-run the checker before acting on a 'worker-N owner observation unavailable' row (it is usually a collection blip, not a closed card)?
- Did you treat a worker-N 'closed on owner (done), never released' row as a lingering slot record (leave it unless its age grows past hours)?
- Did you confirm a core-update drain hold is live (hermes-sync.log core-drain lines) and leave its ready-unclaimed rows alone unless it persists past the 2h max?

## Archive rule (needs the user's standing approval; record it in memory)

Archive a session when **all** hold: not active in the last 30 min, not pinned, its work is done
(card done, PR merged/closed, or its last message says finished with nothing pending), it is not
waiting on the user, and it is not this session. Also archive superseded duplicates (same title,
older, idle) and stale cron/loop ticks.

`hermes sessions archive` matches by filters, not by id, so archive one session safely:
1. `hermes sessions archive --title "<exact title>" --newer-than <age> --dry-run` and check it
   matches exactly the session you mean (narrow with `--source`, `--cwd`, `--after`). It matches only
   ended sessions by default. A Desktop chat is usually still open, and the dry run reports it as
   "skipped", so add `--include-open` for that one session.
2. Re-run with `--yes`. It soft-hides (no deletion). Read back `archived=1` in `sessions`.

If the user's sidebar archive "keeps failing", grep `agent.log` for `session.mutate storage failure` before
retrying. A `structural corruption` reason there is the state.db latch, which refuses every write:
follow patching-local-hermes-runtime, then archive through the CLI.

Never archive pinned sessions, anything with an open PR or open card, or anything ambiguous: list
those for the user instead. Done cards: `hermes kanban archive <id>` only after reporting them.

## Ledger (source of truth across compaction)

Keep `~/.hermes/session-manager/ledger.json`: one row per request (`id, asked_at, ask, route,
target (card/session id), status: answered|dispatched|blocked|done|failed, reported_at, note`) and
`last_sweep`. Re-read it after compaction and on every wakeup; a row with no `reported_at` is a
report you still owe. Also rewrite `~/.hermes/session-manager/status.md` each sweep: one line per
live workstream, under 30 lines, so "what's going on" is one file read.

## Sweep cadence

Schedule a recurring check every 20-30 min while there is open work (a cron job or the session's
own wakeup). Check `hermes cron list` first: if a job like `session-idle-check` already sweeps idle
sessions, build on it or retire it rather than running two sweepers. Each sweep: read, classify, act, archive, report. Report only what changed; stay quiet
when nothing did.

## Reply style

When David asks what a session is doing, lead with his goal and whether it was delivered; separate it from broader attached work before explaining a choice. For preserved unfinished work, inspect the handoff and actual source before recommending its fate: explain what each reusable piece does, what remains broken, and the smallest path to his goal. Stopping an attempt is not abandoning the goal; do not answer renewed interest with another blanket shelving recommendation. Distinguish proposed continuation from execution authorization. Use plain language, not allocation history or card IDs; distinguish preservation, recovery and shipped results.
