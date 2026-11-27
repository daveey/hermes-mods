---
name: kanban-hands-digest
description: "Use when asked what's blocked on Kanban. Clears blockers."
triggers:
  asks:
    - "What's blocked?"
    - "What do you need me to do?"
    - "Is it stuck?"
  not_for: "general session management (use hermes-session-manager)"
version: 1.0.0
author: daveey
license: MIT
metadata:
  hermes:
    tags: [kanban, digest, blocked, human-in-the-loop, cron, auto-unblock]
    related_skills: [kanban-worker, kanban-orchestrator]
---

# Kanban hands-on digest

Clear agent-solvable blockers; consolidate verified human-only actions into **one ordered checklist**.
For an all-session fleet audit, use the inventory-and-session-join procedure in
`references/clearing-blockers-yourself.md` before claiming complete coverage.

## Scope: this session by default

"What's blocked?" means **this chat's cards** (the ones it created or is subscribed to, i.e. its task strip),
not every board. **"What do you need me to do?" is wider**, and so is any "nothing (new) needs you" line you add unasked, such
as the close of a Kanban notice reply. He reads both against the Desktop "For you" panel, which lists every
human-blocked or gave-up card on the gateway, not only the thread you were just discussing. The ask-gate's
"Cards linked to this chat" list is not that set: it omits asks on cards this chat never created. Before any
such line, sweep each board for `blocked` (`needs_input`/`capability`) and `gave_up` cards (`sqlite3 -readonly
<board> "select id,title from tasks where status='blocked' and coalesce(human_request,'')<>''"`, plus
`hermes kanban list` for `gave_up`) and clear them. If a genuine ask remains, leave the line out rather than
restate the ask; the panel already shows it. He calls an answer of "nothing" given while that panel still
shows asks a lie.

The card set for the session-scoped question: Find them from this session's own `kanban_create` results /
task strip, then `kanban_show` each and report the blocked ones. For a session he links (`@session:<profile>/<id>`),
query each board: `select id,status,block_kind,title from tasks where status not in ('done','archived') and
(session_id='<id>' or id in (select task_id from task_chat_links where chat_session='<id>' and coalesce(released,0)=0))`.
The system `sqlite3` is refused on live boards (SQLite live guard): run these queries as a small script under `~/.hermes/bin/hermes-core-python` with `sqlite3.connect('file:<abs path>?mode=ro', uri=True)`; its `-m sqlite3` form does not take a `file:` URI. The main board is `~/.hermes/kanban.db`; `kanban/kanban.db` has no `tasks` table. Cards and parents owned by other gateways exist only in `~/.hermes/kanban/sync-shadow/*.db`: union those with the main board before walking a chain, or remote parents read as missing.
"Nothing running, all waiting" means: walk each card's open parents and its scheduled `wake_check` target down to
the leaf that nothing will move (a decision card, a `ready` card whose events are only `skipped_nonspawnable`,
a throttled queue, a PR wait whose PR is no longer queued: check its labels/timeline and the waiter's exit,
not the card's "waiting on merge train" text), and report that leaf; the cards above it only poll. Then move that
leaf yourself (re-submit, raise its **current** priority, or do the step), not just report it. Read priority before
applying a suggested value: setting 10 lowers a card already at 20. A waiting card's gating parent (acceptance/verify
card a worker made) is often created at priority 0 under a 200+ child: raise it above the queue top and confirm
`queue_note` reads "~0 ahead". To re-place instead, first read its `moved` events: a card already moved back
for hub-local state is refused (`ping_pong`) and belongs on the hub; `hermes kanban move <id> --runs-on
machine:<host> --reason "..."` needs `--reason`, and only portable work (code shipped via PR) moves. For a `ready` leaf, first read its
latest comments: a subagent or chat may already have delivered the work there, and nobody closed the card.
Verify the artifact (hash, served model in `state.db`) and `kanban_complete` it yourself. "0 ahead" counts
only its own board, but `max_in_progress` is global: list `running`/`ready` across every board and check
`~/.hermes/core-update/drain-hold`. The hold stops every new claim while its writer pid
(`hermes-sync core-drain`) is alive (older cores ignore its `max_s`). Compare `date +%s` with `started_at+max_s` and
read `core-drain` lines in `~/.hermes/logs/hermes-sync.log`. A long-expired hold whose pid stays the same: kill that pid
(it is not a gateway; sync retries later). A hold that keeps coming back with a new pid/`started_at` means sync re-arms
it every pass, so killing it only delays the update. The drain counts live Desktop turns, your own chat included, as
busy: reprioritize, then end the turn quickly so the update can apply.
Free slots with cards still waiting ("1/16 busy, 14 waiting"): on THAT machine run `hermes kanban dispatch --dry-run --json`
and read `core_update_drain_hold`, `at_capacity` and `skipped_ineligible` reasons (`labels_mismatch`,
`waiting_for_worker_capacity`). Reach an EC2 worker that has no ssh/tailnet name via hub instance-role SSM:
`aws --region us-west-2 ec2 describe-instances` filtered on tag `Name=hermes-worker-N`, then `ssm send-command`
(`AWS-RunShellScript`) with a base64'd script run as `sudo -iu ubuntu bash`, so `hermes` and `~/.hermes` resolve.
A card pinned `machine:worker-N` that sits `ready` with no worker: on that worker, `hermes kanban show <id>` prints
"not imported here; last pickup skip: <reason>" (`hold`, `budget_exhausted`). For `hold`, read the Board registry
(`python3 -c "from hermes_cli import kanban_worker_node as w; print(w.registry()['machines']['worker-N'])"` from
`~/.hermes/hermes-agent`): `hold_reason` names the owner card and its release deadline (often a core-rollout
drain). Don't move a card whose worktree lives on that worker; report the hold's owner and release time instead.
The fleet panel's header "waiting" counts unleased `ready` cards whose assignee is a dispatchable profile. Each machine
row splits them into the cards it can take and those it can't (labels, pins), so read the row, not the header, to see
whether that machine has real demand. Verify it live by fetching `/api/plugins/runners/v1/fleet` on the dashboard port
(the session token is in the served `/` HTML), then hand-count the replicas. Raising
`max_in_progress` hands the new slot to the highest-priority ready card on any board, not to yours. If direct execution starts while the card
is queued, recheck its claim before editing or publishing and hand off to a newly admitted worker; queue
recovery must not create competing implementations. Also report two states that wait silently:
- `triage` with a human `block_kind` (`needs_input`/`capability`): block-loop detection moved a repeatedly
  re-blocked card there. It still waits on the user, and the latest `block_loop_detected` event holds the ask.
  Filtering on `status='blocked'` alone (as the digest script does) misses these.
- `scheduled` cards whose wake time has passed, or that have no wake time at all (no `wake_at` on the
  latest `continued`/`scheduled` event, or a wake cron that no longer exists): they sit unrun
  indefinitely. Re-queue with `hermes kanban unblock <id>` (it accepts scheduled cards). A `needs_input`
  card parked in scheduled is an ask of the user; report it.
"Is it stuck?" about a long elapsed time in a Desktop worker list: that figure can be card age (`now - task.started_at`, spanning every run and scheduled wait), not the current run. Judge from the current run's start, its `outcome` history and the latest event/comment time. Desktop lists cards from every gateway, so when `kanban_show` says "not found", read the bare id with `cross_task read` (it searches all gateways) instead of hunting boards. A sidebar BLOCKED group headed `<assignee> · mac-mini` (no session) is a card on the mini's own non-synced board (`~/.hermes/kanban/boards/bb/kanban.db` there), absent from the hub's sync-shadow and remote-index: from the hub read it with `tailnet_exec host=mini` (`hermes kanban --board bb list`, or a python sqlite query), since plain `ssh mini` does not resolve there. Such cards are BB work the hub migration left on the mini; the fix is moving them to the hub, not unblocking them in place.
"It shows waiting but isn't waiting on anything": he is reading the session panel's per-card row, not the sidebar's "Waiting on N cards" badge, so never answer "that's by design, the chat waits on its card". A card whose board status is `running` but whose `kanban_show` `execution_observations` say `state: unknown` (`adapter_error`, `missing_qualified_identity`) is drawn under Waiting with "evidence unknown": that is a display defect in the observation reader, not a wait. Say the card is running and the panel is wrong, then search open cards on every board for the panel's own wording (`title||body like '%evidence unknown%'` / the `unknown_reason`) before diagnosing further: the symptom hits every card on that board at once, so another chat has usually filed the owner card already. Comment this card on it as a second live case instead of starting a second fix.
"Waiting on parent" with no parent shown running or blocked: walk the session's `todo` cards' open-parent chains (across sync-shadow) to the leaf. It is usually a `triage` card whose latest relevant event is `contained` (`kanban_contain` by a review/authority card): containment is never dispatched and never counts as blocked, so the panel lists it as "other" and every descendant reads "on parent". For each, read the contained event's `policy.authority.task_id` and its status, then search open cards for an owner of the release; a contained card whose authority is done/archived with no owner sits forever. If the leaf is not linked to this session at all, the chat adopted a child without its parents.
The pink "Blocked upstream" section ("waiting on blocked parent t_x (blocked|triage)") paints the row red and names only the parent's status, not its reason, and its "nothing needed from you" header ignores asks on parents another chat owns. Answer with each parent's own reason: its `human_request`, its `spend_budget_triaged` comment or its `contained` event. Re-test that reason live before relaying it (a "put the checkout back on main" ask is disproved by `hub-worker-exec <w> -- 'git -C ~/.hermes/hermes-agent rev-parse --abbrev-ref HEAD'`). If it is stale, comment the evidence and unblock the parent yourself.
"Nothing is blocked" is only half an answer when he can see red: in the same reply, name what paints it. The session row's stripe logs every change with its reasons in `~/.hermes/logs/desktop-stripe.jsonl` (`grep <session_id>`; e.g. `outcome:decision` = an open approval tile, `kanban-blocked:N`, `stalled`, `review:<pr>`). For the server's counts on that row, run `annotate_kanban_activity([{"id": "<session>"}])` from `hermes_cli.kanban_session_activity` (under `hermes-core-python`). `todo` cards waiting on a parent show up as `kanban_parked`, not blocked. A "Question" tile he already answered that reads "Decision recorded; delivery unconfirmed" is not a blocked card: it stays red until the owning chat returns an `action_request` receipt, which a chat stuck in a long tool call can't do. Say his answer is saved and nothing is left for him. Desktop UI strings may exist only in the newer Mac build: grep `apps/desktop/src` once, never whole `~/code` trees (minutes, and an update pause interrupts it). The status-bar task pill counts the whole board, so its red can come from other chats' asks. If none of these explains the red, ask him for the hover text.

Before presenting an unblock request:
1. Read the latest task events; delayed crash notices do not establish current state. Distinguish worker failure from an explicit human ask—never substitute the task body's opening sentence.
2. Report cause → current execution state → next verified step. Read a completion notice's full result: implementation may be done while publication/deployment is handed off. Advance that existing landing owner before calling the fix delivered. For genuine asks, name the action, device and completion condition; never repeatedly requeue a refusal or switch providers to evade it.
3. `hermes kanban stats` "Waiting on you: N" and diag `waiting_behind_user` are not a list of asks: they include quota-wall holds (`blocker_auth`) and asks that went stale. Check each premise live (`gh workflow list`, device state, `kanban_show` events) before it goes on his list; clear or archive the false ones yourself.
4. When the user calls the messaging useless, fix its producer and rendered surface, not just the explanation. Keep operational pauses visible as agent issues without Done buttons. Test failure → nonhuman pause → genuine human ask, including rendered buttons/counts; helper tests alone miss misleading controls.

## This user: clear it yourself, never hand him cards

Treat recurring stalls or wrong chat associations as recovery plus prevention, not repeated status replies. Trace the original producer and follow-up inheritance before relinking: a valid fallback owner can conceal missing project context. Repair existing cards and the producer; report relinking, prevention tests and deployed proof separately. Relinking alone is not recurrence prevention.
1. Sweep blocked cards and clear agent-operable causes through supported transitions (recipe: `references/clearing-blockers-yourself.md`). Keep true approvals and safety holds; verify a new claim or completed acceptance, not merely `ready`.
Applies to every board-status reply, including "why are only N running": what waits on him is written as concrete actions grouped by where he does them (reply here / phone / Mac / when on site), each with the device and what done looks like. No card ids, no "see t_x", no table of cards for him to open.
2. Bring genuine decisions to him INLINE as a `clarify` form. Use one question per decision in plain words, recommended option first, and batch the low-stakes defaults into one "accept all recommended" question. Never answer with card ids or links, or with "answer in a comment on t_x". Then record his answers on the cards yourself (comment the decision on the card and on every card waiting on it, then `hermes kanban complete` the decision card).
   Not a decision: "close it unproven, or keep waiting?" on an approved goal whose acceptance is unmet. The
   ask-gate refuses it (`replan_hold`): write a new approach to reach the acceptance on the owner card, archive
   the stale decision card and unlink it from its child, then send the work to a reviewer-plus-executor
   using separate review/execution custody and the supported review handoff. Request `class:deep` if useful,
   with provider omitted and never mandatory; no exact-model exclusivity. Only a changed limit (spend,
   permission, lower acceptance) or a hands-only step goes to him.
   The ask he sees must be the decision itself in household terms ("should unattended agents refuse dangerous commands?"), never card mechanics ("unblock = yes, archive = no", "option C"): he reads a blocked card and finds nothing to do. If the `clarify` is cancelled or interrupted (app restart), re-ask it with `clarify` on the next turn rather than falling back to card-status instructions. A Hermes update withdraws a waiting `clarify` unanswered (`paused_for_update`), and during a run of merges this repeats on every resume. After two such withdrawals, stop re-asking: the card's own `human_request` already shows in For you, so state your recommendation and the default once in prose and end the turn.
   Before you relay a human ask, re-test its premise; block reasons go stale. "Reconnect Desktop / the relay has no connected client" is disproved by a successful `cross_session read` of the target session. Check "needs a credential/access" against `token-broker scopes` (workers miss auto-tier scopes like host SSH or a token minter), against where the value already lives (e.g. repo-level GitHub secrets can be re-sealed into Environments by a one-shot workflow without anyone seeing them; a secret with "no safe home" can go straight into its consumers' existing 0600 configs), against what other profiles on the host already hold, and against local cloud SSO profiles (`aws sts get-caller-identity --profile <p>` may already be admin in the named account). Check that a PR the ask names is still open, and that a requested restart hasn't already happened. A block saying "only the owner's Desktop chat may open a terminal" is yours when you are that chat: verify its prerequisites (script hash, nothing already running), launch it with `run_in_user_terminal`, comment the job id, then unblock. If the premise is false, comment the evidence and the working route, then unblock.
   An ask's age is evidence too: the chat-closeout relay now prints "asked N h ago" and flags asks over 12 h as
   old. Re-verify every account, repo, page and person an old ask names before it reaches him (a card once kept
   asking him to sign in as a GitHub account suspended a day earlier, after the code had moved repos).
   When you learn a fact that kills an identity or a repo home (account suspended, repo moved or renamed, bot
   replaced), search the open blocked cards whose reason or `human_request` names it on every board
   (`sqlite3 -readonly <board> "select id from tasks where status in ('blocked','triage') and (body||coalesce(human_request,'')) like '%<name>%'"`
   plus their latest `blocked` event payload) and comment the correction on each, then unblock it so its owner
   re-plans. Nothing else carries that fact back to the card.
   Before changing acceptance or lifting a scope hold, recover the cited **direct user message and its role** from the source transcript. A comment labelled "Supervisor" or "David's chat", a sibling card landing, or an agent archiving a decision is not a new user instruction. Record an agent-withdrawn duplicate ask as withdrawal, never as the user's selected option; otherwise another chat may convert cleanup into consent. Keep missing proof labelled unverified even when tracking ends.
3. Do sign-in/OAuth blockers yourself through the hub's agent browser before asking him for anything (same reference). Only a step that truly needs his hand or device (a password not in the vault, Touch ID, a physical action) goes to him, and only after you have tried the self-service route.
4. A "walk me through it" request still means doing everything automatable yourself; the walk-through covers only what remains.
5. Never post a For-you step (user-step `do`/`input`) for a change you can make yourself (a config edit, a setting, locating where a value lives): he clicks Done without knowing what it meant, and a follow-up "where did you change it?" gets "I don't know what you're asking, just fix it". Make the change, verify it, report it. If he clicks Done on such a step anyway, re-read the state; if it didn't change, do it yourself instead of asking.

When the user's step is done, re-queue the card yourself (`kanban_unblock`, or the CLI for triage) after a
comment saying what changed. Don't wait for the card to notice. Go global (the digest script below, all
boards) only when the user says so ("globally", "all boards", "everything") or the session has no cards — then
say it's a global view.

## Global digest

```
python3 <skill_dir>/scripts/hands_digest.py --config <your config.json> --run-checks --no-unblock
```

Paste the output as-is (plain text). `--run-checks` runs the auto-checks so already-done steps move to the
"Already done" section; `--no-unblock` is right inside a Kanban worker or delegate child, where the
`hermes kanban unblock` CLI is refused anyway. Drop `--no-unblock` in a normal session to let passing
checks unblock their cards. Other flags: `--json`, `--max-chars N` (shrinks item text to fit a chat post),
`--checks-only` (silent unless it unblocked something; for a frequent cron), `--dry-run`, `--home`.

## What it reads

Every board (`$HERMES_HOME/kanban.db` + `kanban/boards/*/kanban.db`, `_archived` skipped), read-only.
A card is included when `status='blocked'` and its latest `blocked` event has kind `needs_input` or
`capability`, or it was created blocked (`initial_status`) — then the body's structured keys or its
`Blocked until:` line are the reason. `transient`/dependency blocks are not human work.

## Block-reason format workers should write

```
ACTION: <one imperative line the human can do, with the device>
DEVICE: <group name from config, e.g. iPhone>
UNBLOCKS-WHEN: <observable condition>
CHECK: <check name> [args]          (optional; must exist in config `checks`)
DEADLINE: 2026-10-18T19:55-07:00    (optional; hard deadline, e.g. an invite expiry)
```

Keys may be on separate lines or run together in one line; `ACTION:` may repeat. Free-text reasons still
work through heuristics (imperative sentences kept, shipping/status chatter dropped, `expires <date>` and
`before/after HH:MM PT` picked up), but the structured form gives a clean one-liner.

## Config (JSON; keep the personal one outside this repo)

`references/config-example.json` shows every key:
- `groups`: ordered `{name, keywords, weight}`; a card goes to the group with the highest weighted keyword
  count. Display order = list order, after the TIME-SENSITIVE block (deadline within
  `deadline_horizon_hours`).
- `shared_steps`: dedupe one step shared by several cards. `sentence` regex picks the clause (only the
  matching clause is removed from each card's own action), `value` regex extracts a number, `agg`
  (`max`/`min`) combines them, `label`/`check` format with `{max}`.
- `rules`: `{match, group, check, action, unblocks_when}` applied by regex to the reason.
- `cards`: per-card overrides by id.
- `checks`: `{name: {cmd, unblock, timeout}}`. `cmd` is a shell command formatted with the check's args
  (`{0}`, `{args}`). Exit 0 = satisfied, 1 = not yet, else error; the first stdout line is shown. With
  `unblock: true`, a passing check bound to a card runs `hermes kanban [--board B] unblock --reason
  "auto-check ... passed: <line>" <id>`. Checks bound to a shared step only mark the step done.

## Scheduling (Hermes cron, no-agent)

Two no-agent jobs pointing at a small wrapper in `~/.hermes/scripts/` that passes your config:
a morning digest (`0 8 * * *`, `--run-checks --max-chars 3400`) and a frequent auto-unblock pass
(`every 15m`, `--checks-only`, empty stdout = silent). Prove delivery once with `hermes cron run <id>`.

## Tests

```
uv run --with pytest python -m pytest -q -p no:cacheprovider skills/devops/kanban-hands-digest/tests
```


## Pitfalls

- Before any "waiting on you" row or decide prompt, reconcile it against his latest chat text: an instruction he typed ("cancel X", "close that card") is the decision, so carry it out that turn and withdraw the open prompt it answers. Re-listing it as his ask, or re-posting its buttons, repeats a question he already answered.
- Never write a bare code ("A", "Option 2") as a `human_request.choices`/`action_request`/`clarify` label: put the meaning in the label, add one plain `explain` line and set `recommended` on your pick. The For you tile shows only the labels, and the letters' definitions sit in a message he can't see.
- A `mode=ro` sqlite open can fail transiently while a writer truncates the WAL; the reader retries,
  then falls back to `query_only`.
- Checks that read a remote DB must not `.backup` a big live database per call (tens of seconds); open
  it `-readonly` with a busy timeout.
- Inside a Kanban worker the `hermes kanban` CLI refuses mutations, so auto-unblock only works from cron or
  a normal session; run on-demand with `--no-unblock` there.
- **Your own reply text can become an ask.** On Desktop, the user-steps output transform publishes any reply
  sentence that tells the user to do or decide something as a "For you" prompt.
  - Sentences that did this: "Answering either with 'no action needed' clears it", a closing note that an
    option was deliberately not taken, a reminder that an already-open request "needs a Mac name, or
    'Wait'" (published as a duplicate of that request, more than once), and a "Still to do: I queued a card
    that will ..." note about agent work (he asked "is this waiting for me?").
  - A `::user-step` directive holds only the one action he must take, never agent progress, a queued card's plan
    or background facts about other cards: it renders as a For you ask with an answer box. Say those in plain prose
    and that nothing needs him. Never reuse an open request's `us_…` id for other text: that id is the request's
    alias, so an answer there lands on that request.
  - State settled facts as statements. Put a genuine decision in one `action_request`; when one is already
    open, don't restate it at all: the For you panel already shows it. Even a neutral closing list ("You still
    have two decisions in For you: …") or a "Your choices: Yes / Change / No" block gets published as a duplicate.
  - `session_outcome {kind: decide}` publishes its own For you tile. Don't also file an `action_request` for the same ask (that makes two tiles); pick one. Compute any count you put in the ask with code first: a miscounted tile can't be edited, only duplicated.
  - After each reply, check the hook's open-request list for an auto-filed duplicate or stale prompt and
    remove it with `action_request {"withdraw": {"requestId", "reason"}}` (alone, no `request`). It needs the full UUID: the hook's 8-char prefix returns 404, so grep the saved hook output file for `"requestId": "<prefix>`. Readback
    shows `state: withdrawn`. To correct one that is still needed, re-send it with its `requestId` and
    `expectedRevision` = current revision.
