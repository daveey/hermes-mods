---
name: kanban-card-churn
description: "Use when one Kanban card has many runs, comments or a huge context and still isn't done."
version: 1.6.0
author: daveey
license: MIT
triggers:
  asks:
    - "why is this card taking so long and has so much context?"
    - "this card has 20 runs and 90 comments, clean it up"
    - "what can we learn from this card, and stop it happening again"
    - "how will we avoid this failure mode in the future"
  not_for: "many red cards at once (use kanban-red-sweep); one failed run with a clear error (use five-whys)"
metadata:
  hermes:
    tags: [kanban, churn, context, cleanup, postmortem, card-design]
    related_skills: [five-whys, kanban-red-sweep, kanban-orchestrator]
---

# Kanban card churn: measure it, clean the card, fix what made it

A churned card is old, has many short runs, a long comment thread and a growing pile of files every run must
read, and its acceptance is still unmet. The card is rarely slow; it is waiting, re-reading and re-parking. This
skill has four parts: **measure**, **clean up the card**, **check the producer**, and **check the platform mechanism**.
Change only what the evidence shows failed; a spend-budget hold that correctly stopped further runs is not
itself a platform defect.

For performance acceptance churn, read `references/performance-convergence.md` before replanning.

Answer the user's "why" in text as soon as step 2 is done, before any change. Then carry on.

## 0. Pick the card (when the user names none)

Rank open cards on the fleet replica by runs, then read the top ones. Skip standing cards and shepherds: they
recur by design.

```sh
~/.hermes/bin/hermes-core-python -m sqlite3 ~/.hermes/kanban/sync-shadow/default.db "select t.id,t.status,count(r.id) runs,
 (select count(*) from task_comments c where c.task_id=t.id) comments,
 cast((strftime('%s','now')-t.created_at)/86400 as int) age_d, substr(t.title,1,70)
 from tasks t join task_runs r on r.task_id=t.id where t.status not in ('done','archived')
 and r.outcome not in ('released','reclaimed')
 group by t.id order by runs desc limit 15"
```
Count only runs that did work: a dispatch loop can leave 200 zero-second `released` runs on an
otherwise healthy weekly check. Also look at runs in the last 7 days and total run-hours: a card with many
runs, many hours and steady output (one PR per day) is a long serial pipeline, not churn. Clean its
context and waits, but do not restructure it.
The replica is for ranking and for per-run outcome counts (`task_runs.outcome`, `started_at`, `ended_at`) only.
Everything you act on comes from the CLI on the owning gateway.

## 1. Measure (read-only)

Run these on the gateway that owns the card (a replica can be days stale).

```sh
hermes kanban show <id>            # status, wake check, parents, body, latest summary
hermes kanban runs <id>            # one line per run: outcome, elapsed, start
hermes kanban context <id> | awk '/^## /{if(n)print s,n; n=$0; s=0} {s+=length($0)+1} END{print s,n}'
hermes kanban show <id> --json     # task.body, comments, events (read stdin as bytes: bodies are not ASCII)
```

Write down these numbers; the reply leads with them:
- **Age vs work:** days since creation, number of runs, sum of run elapsed time. Hours of work over weeks of age
  means the problem is waiting, not effort.
- **Run outcomes:** count `blocked`/`scheduled` runs, then inspect their summaries and artifacts before calling
  them poll-and-repark: a run can deliver a source verdict while final acceptance remains open.
- **Context a run really gets:** the section sizes from `kanban context`. The thread is already tail-capped (30
  comments), so the usual bulk is `Parent task results` plus the files the body orders every run to read. Size
  those files (`wc -c`) and the project skill the body loads. Comments that contain their own `## ` headings
  split the awk output into extra sections; add those back to the thread.
- **Workspace:** file count and `du -sh`. Hundreds of numbered one-off scripts and repeated full checkouts are
  per-run litter.
- **Comments by author and day:** how many are status relays from other cards and chats rather than work on this
  card.
- **The wait chain, to the bottom:** follow the wake check and parents until you reach a card with no runs or a
  host that is not there. For each link note its status, its home gateway and whether that gateway is alive. A
  card homed on a stopped or held worker can never run, and a move request for it executes on that same dead
  home.
  If the bottom is a decision waiting on the user, read its `human_request` and put the exact choices at the top
  of your reply: that one answer is worth more than any cleanup.
  A chain can also end at a decision an agent took for the user under a standing default ("keep paused"), with
  no ask outstanding and an archived prerequisite. Runs in the last 7 days = 0 on an old card is the sign. Then
  the card needs no cleanup: report what was paused, by whom, on what evidence, what resuming costs, and ask the
  user resume or stop. Do not restructure a paused graph.
- **App or version churn:** how many times the thing under test was redeployed during the card's life.

## 2. Classify the causes

Use `five-whys` for the chain; every cause below needs evidence from step 1. Most churned cards have three or
four of these at once.

| Cause | What it looks like | Cleanup | Prevention |
|---|---|---|---|
| End-to-end test used as the bug finder | each real attempt finds one new defect, then waits for a fix | run the isolated fixture now; record every failure at once | fixture gates must pass before any real attempt |
| Heavy fix cycle | each defect costs review + package + human activation | batch the known defects into one cycle | body rule: no activation for one defect while the fixture shows more |
| Omnibus acceptance | many gates, "not done until all proven", nothing ever closes | per-gate table, each row pinned to a version | one row per gate from day one |
| Evidence reset on every deploy | all gates reproven after each new version | reset only rows whose code changed (diff manifests) | same rule in the body |
| Card used as a message bus | most comments are relays ("source only, nothing activated") | state file; body tells others not to relay here | relays go to the state file |
| Append-only state files | checkpoint and evidence logs in the hundreds of KB | freeze them; small overwritten state file | size cap written in the state file header |
| Two cards hold the same state | integrator and slice both track the gates | name one owner of the gate table | one table, one owner |
| Wait on a dead home | wake check never passes; producer homed on a held worker | decouple: do the step here or fix forward | body rule: never wait on a card homed on one worker |
| Pinned to a gone machine | `runs_on` names `machine:<w>` and `<w>` is not in the worker registry (autoscaled away); dispatcher notes "nothing is watching" | `hermes kanban move <id> --pool --reason ...`, then park it on its real producer | never pin a card to one machine for state: state goes in an attachment |
| Host-local state file | the card moved hosts and its `STATE.md`, checkpoints and prepared tests stayed on the old disk (scratch workspaces are per host) | rebuild it from the newest run comment, attach it to the card | state file is a card attachment, re-attached at run end |
| Resurrecting a failed run | days spent making a sealed or poisoned run pass | keep it as failed evidence; start a fresh run | body rule: fix forward, fresh run |
| Consumable authorization | "both sends used", then hours re-asking the user | quote the user's standing words in the state file | authorization and its scope in the body |
| Single-route dependency | one reviewer or model route quota-walled for days | name a fallback | named fallback in the body; never silent |
| Finished parents kept for the record | 10 to 20 done parents; `Parent task results` is half the context | unlink them, list ids in the state file | recipe: list finished prerequisites in the state file, do not link them |
| Other cards park on this one | consumer cards post a recheck comment every wake | state file rule: no recheck comments | fix the consumers' skill; check first whether it already says so |
| Workspace litter | hundreds of numbered scripts, several full checkouts | note it; delete only your own files | body rule: reuse one checkout, remove one-off scripts at run end |
| Handshake phrase mismatch | two cards wake on each other's comments; a request sits unanswered until a backstop | post one pointer that matches the waiting card's wake phrase; never fake the answer | fixed tokens (`X-REQUEST <id>@<sha>`, `X-GRANT`, `X-REFUSE`) in both bodies; wake checks match the token only; the sender reads the receiver's wake check after posting |
| Self-continuation loop | dozens of `continued` runs in a few days, then `continuation_exhausted` and a split into slices | none needed once split; check the slices are not parked on the same wall | body: acceptance in slices from the start, each with its own done rule |
| Adversarial review as gate generator | every slice ends with an adversarial review whose new false-green finding the orchestrator turns into the next slice; dispositions pile up, nothing ships | triage findings against the frozen acceptance list: (a) breaks a listed item or regresses vs main = blocks ship; (b) everything else = one follow-up hardening card | the orchestrator's disposition names that (a)/(b) rule from the first slice; never grant a slice for a (b) finding |
| Infeasible acceptance found late | a hard cap (time, memory, budget) the work cannot meet, discovered after 100 runs | record the measured gap and the options in the state file | first run of any card with a hard cap is a feasibility probe that reports the margin before building |
| Paused graph left open | every card `todo`/`scheduled` behind a pause someone applied by default; clock-only wakes weeks out | put the exact resume-or-stop decision to the user; archive the graph on stop | a pause names who applied it, the user's words or the default used, and the one sentence that resumes it |
| Coordinator fan-out | one card created 100+ child cards, linked each as a parent, and has one `blocked: await linked X` run per child | state file with position and wait chain; no restructuring | batch obstacles found in one pass into one child; link a child only while it gates; wake on its completion |
| Poll-and-repark | runs that only reread state and reschedule | wake on the real producer event only | spend budget; wake checks on events, not timers |
| Terminal wait on own contained producer | watcher waits for done/archived, but that producer is contained under the watcher’s release authority | supersede the contradictory scope with one bounded authority-disposition run; retain independent spend gates | before replacing polling with a terminal event wait, verify the waiter does not still own an unresolved containment release required for that event |
| Ask parked on a dormant orchestrator | a card parked awaiting its owner's decision; delivery may be delayed or superseded | read current status and newer owner decisions first; answer only an outstanding question, without manufacturing user authority | `awaiting={kind:'chat', why:<question>}` posts `decision_requested`; after 2 h unanswered the platform records agent-owned recovery, preserving the schedule and answer probe, never an automatic human request. Verify `decision_unanswerable` and current state; only a concrete human-only action justifies `needs_input` |

## 3. Clean up the card

Before prescribing a lifecycle operation, verify the target's board mode, current run versus ended run,
goal mode and applicable custody/decision episode together. A private board is not necessarily local/off.
Use the existing rejected-retain-custody guidance; a definitive refusal is not a successful terminal receipt.
Check the proposed producer is still open before naming it as future work. When reads are isolated, request
one consolidated authorized export rather than discovering each prerequisite through another waking comment.
For delayed decision notices, reconcile newer owner decisions before posting: an answered notice needs no
new waking comment or repeated architecture choice. Report three separate facts: chat turn activity,
administrative task execution, and changed product acceptance. A claimed repair proves only the second;
lead with the remaining delivery gate, not “running again.” These checks grant no release or retry authority.

Before editing and again before promotion, re-read the newest body, budget events and comments. For a foreign spend-held card, preflight `kanban_supervisor_request` operator `spend_promote` with `dry_run=true` against the current episode before preparing a release. Its `spend_budget_decision_ready.producer` must name this decision card; a producer of `chats` is not delegated by prose in a cleanup card. A `spend_scope` refusal requires disposition by that authenticated managing owner, not ordinary unblock, a new relay card, or a copied approval. Keep prepared state explicitly unapplied until body, attachment and finite allowance are read back on the owner.

**When you are the managing chat, do the release yourself; don't delegate it.** A cleanup card you spawn can measure and draft STATE.md, but it is refused `spend_scope` and blocks back on you, which costs a full round-trip. From the chat, do this:
1. Fetch the draft from the worker's workspace (`hub-worker-exec <worker> -- cat …/STATE.md`).
2. Attach it to the source: `hermes kanban attach <src> STATE.md --name STATE.md`.
3. Post one comment that gives the ONE-RUN scope and what that run must not do.
4. Run `hermes kanban promote <src> "<reason>"`, then read back the status.

Once the cleanup card's step is done, archive it.

**Every bounded run needs its successor scope posted before the card is released again.** The one-run grant is used up by that run, while a parent repair or a finished review can auto-promote the source. On each run-end report, post the next one-run scope as a comment on the source: fix or refute every finding in one batch, no weakened limits, then one review and stop. Do this before or while it waits, so the next run reads the new scope rather than the spent one. Before answering a creator-decision notice, identify the named disposition owner: delivery to a subscribed chat does not transfer authority. If another chat owns it, send evidence, not a competing grant or contract. A disclaimer that the owner takes precedence does not neutralize contradictory requirements. If you already posted them, explicitly withdraw each conflicting requirement, name the governing disposition, and read back the worker's state before claiming recovery. If `spend_promote` refuses an unsatisfied parent, retain the hold: it does not necessarily transition to dependency-gated todo. Check for a managing-owner promotion successor before creating or retrying anything; when one exists, withdraw competing prepared grants explicitly and verify that successor's exact scope and parent.

1. **State file.** Write `<workspace>/STATE.md` (under 8 KB, overwritten, never appended). Sections: live version;
   gate table (gate, proof mode, status at this version, evidence link, what is left); root cause of the current
   failure; decisions; next action in order; standing authorization with the user's exact words, date and scope;
   reviewer or model routes with named fallbacks; never-replay list; where the frozen history is.
   Then attach it: `hermes kanban attach <id> STATE.md --name STATE.md`. Scratch workspaces are per host, and a
   card that moves to another worker (pool placement, a rehome, an autoscaled worker going away) does not take
   its workspace along; attachments sync with the card. The body says to read the newest `STATE.md` attachment
   and to re-attach the overwritten file at run end.
2. **Gate table instead of new cards.** Split acceptance into rows. Keep the original gate text word for word.
   Mark each row `fixture` or `real`. Do not open a card per gate unless the user asks for cards.
3. **Choose the least invasive proof before building a fixture.** For installed behavior, prefer read-only log
   evidence plus authoritative state readback over custom fault-injection tests. Specify positive, operation-
   correlated evidence, complete log/rotation coverage, and the final state required by the original acceptance;
   absence of errors, a generic replay, or an optimistic UI change is not success. If logging is insufficient,
   add narrowly scoped tracing through the normal review/CI route and leave the live gate UNPROVEN until a
   natural qualifying event. Use an existing isolated regression harness for code coverage; do not turn a
   missing live observation into a new app/backend/tunnel/fault-injection project without a separately scoped,
   bounded authorization. When a harness is appropriate, record PASS, FAIL, UNTESTED and harness errors
   separately. A harness that crashes is not a product failure.
4. **Body.** Save the old body to a file in the workspace first. If the body is the user's mandate (scope, limits,
   acceptance in his words), keep it verbatim and append a short HOW TO WORK block. Replace the body only when it
   is mostly procedure that caused the churn (for example an order to read a growing file every run). Either way
   it must say: read the state file first; gates close one at a time; fixture first; batch fixes; fix forward;
   never wait on a single-homed card; status goes in the state file. Keep the done rule ("block naming the exact
   gate, never done with a gate unproven").
5. **Parents.** First check the core the card's workers run: `git -C ~/.hermes/hermes-agent merge-base
   --is-ancestor 6291d495 HEAD`. With it, the context shows only the 8 most recently completed parents and
   names the rest, so leave the links alone (they are the graph's record) and confirm with `kanban context`.
   Without it, unlink parents that are done or archived and list their ids in the state file. Leave open
   parents linked either way.
6. **Dead waits.** Do not unhold, restart or touch a held machine. Remove the dependency instead: record the
   failed attempt as evidence and make the next action something this card can do. Check every card in the
   chain for a `machine:<w>` in `runs_on` against the worker registry (skill `hermes-workers`); a worker that is
   gone means `hermes kanban move <id> --pool`. Two cards waiting on each other can both sit in triage: promote
   the producer first, then park the consumer (`schedule --awaiting card:<producer>` refuses a triage producer).
   Before replacing disposition wakes with a terminal-only wait, inspect containment authority: a consumer
   that owns the producer's next containment release cannot wait only for that producer to finish. Settle
   the exact episode through a live scoped authority decision, preserving independent spend admission,
   then wait on the actual next producer event. A prohibition on release plus a terminal-only wake needs
   an explicit superseding authority decision, not polling or an ordinary unblock.
   **A frozen card always has an owned next step.** Before ending a containment disposition, name the exact
   episode, the live authority or authenticated producer that can change it, the bounded action that owner
   will take, and how the decision reaches that owner. A completed authority and “preserve triage; maybe
   reconsider later” are not executable next steps. Deliver the missing decision to the managing owner via
   supported custody/reopen paths, preserving spend, human and dependency holds; do not resume it yourself,
   manufacture a fresh allocation, or start a timer. An intentional stop must say who owns reconsideration
   and its trigger, or explicitly retire superseded tracking through the authorized route.
7. **Retire superseded tracking.** When bounded successors have taken over every remaining acceptance criterion, reconcile the original spend-held umbrella in the same handoff. Verify the successor's current state and unchanged acceptance, record the transfer on the original, then archive the obsolete tracker and read back its authoritative status. Do not mark unmet acceptance done, reset its spend allowance, or leave instructions to preserve triage indefinitely. Keep the genuine final acceptance owner open; archiving duplicate tracking must not waive its gates.

8. **Tell the thread once.** One comment: who you are, on whose instruction, what it supersedes, the files
   changed. Then read the status back. Promote or reschedule only a card that cannot start (triage, or a wake on
   a dead home). A card parked on a valid event wake with a backstop stays as it is.

Gate statuses you copied from run summaries rather than verified live are marked so in the state file, with the
run they came from; the owner corrects them on its next run.

For an automatic replan prerequisite, read `references/automatic-replan-handoff.md` before completing.

## 4. Change the producer

A cleaned card whose producer is unchanged is a symptom fix. Find what wrote the card and what kept feeding it,
and change that in the same session:
- **For worker-hours alerts, re-read the installed updater policy before interpreting reclaim counts.** An earlier idle-only owner can be superseded by a later approved bounded automatic-debt drain. Verify the introducing state commit and actual attribution function: automatic drains may deliberately omit `explicit_operator`, so missing attribution is UNKNOWN, not proof of a routine-policy violation. Reclaim slot-hours overlap total occupancy and are not measured lost work. Keep frozen report, current owner-DB and fleet-replica denominators separate. Audit missing product markers before treating product CI/review/Hub titles as infrastructure; retain negative controls for genuine Hermes UI work mentioning that product.
- **Verify squash-landed producer fixes by the landed commit and exact installed source blob.** A reviewed PR head need not be an ancestor after a squash merge; an ancestry miss alone is not a deployment failure. Distinguish legacy count-only cards from fresh deterministic digest reports before commissioning another fan-out fix, and reuse a pending authorized producer request rather than inventing a second identity.
- **First check whether the producer was already fixed.** Grep the skills for the rule you are about to add. If
  it is there and the symptom continues, the cause is cards created before the fix or a skill copy that never
  reached the workers; say which, do not add the rule twice.
- **The skill or template that created the card** (project skill, orchestrator recipe, decompose prompt): add the
  rules from the Prevention column that applied. Typical edits: a state file and gate table at creation; proof
  mode per gate; fixture before real; authorization and fallbacks in the body; no instruction to read a growing
  file whole.
- **The project skill the card loads:** if it tells runs to append to a checkpoint or evidence log, change it to
  the state file.
- **Sibling cards from the same producer** that are still open: apply the same body rules now if they show the
  same numbers; otherwise leave them.
- **Platform gaps you met** (a shutdown that leaves cards homed on the stopped worker, a wake check that cannot
  see a dead home): one comment on the open card for that area, named in your reply. Not a new card.

Verify the prevention the same way as the cleanup: read the edited skill text back from where agents load it.

## 5. Root-cause fix: move the rule from prose into the mechanism

Before proposing platform work, distinguish a missing mechanism from one that acted correctly. When a
spend hold fired, first repair the work plan: retain the original acceptance, attach completed proof and a
never-replay list, and bound the next run to missing-gate verification or a concrete handoff. Do not turn
budget recovery into another platform project merely to satisfy this checklist.

1. **For every cause you named in step 2, ask where a machine could have stopped it.** Write one line per cause:
   the moment it happens (a comment is posted, a card is parked, a parent is linked, a worker is held, a run ends
   unchanged) and the check that moment could run. Typical answers:

   | Cause | Point of failure | Mechanism |
   |---|---|---|
   | Handshake phrase mismatch | a comment is posted on a parked card | the comment command tells the sender the card will not wake; wait on the sending card, not on a phrase |
   | Finished parents fill the context | the context is built | cap the section, name the rest |
   | Noise comments force narrow matchers | a wake probe counts comments | filter by the sending card |
   | Poll-and-repark | a run ends parked with an unchanged summary | the spend-budget hold fires earlier and names this skill |
   | Wait on a dead home | a worker is held or stopped | the hold rehomes its open cards |
   | Nobody noticed | no detector | the step-0 query on a schedule, commenting on the owner card |

2. **Search before you build.** Check core, plugins and open cards for the mechanism. If it acted correctly,
   record that result and stop platform investigation. If it failed, establish why (threshold, deployment or
   entry path) before changing it. If a card already owns that defect, add evidence there, not a second owner.
3. **Rank and pick.** Prefer a change that removes a whole class for every card over one that adds a new
   watcher or a new kind of card. A fix that creates cards or runs on a timer is the last resort: the platform
   working on itself is the cost you are trying to cut.
4. **Tell the user what you will build, then build it.** State the picks in the reply. If the user has asked for
   root-cause fixes, or has standing approval for platform fixes, start in the same turn; otherwise ask once.
   Use the repo's own landing route (worktree, tests, independent review, required CI), never a direct push
   around it. A neutral review prompt: "trace this logic and report what you observe".
5. **Each mechanism gets a regression test that replays this card's failure** (the real phrase, the real parent
   count), a test per entry path (CLI and agent tool are two), and a stated limit where it does not reach.
6. **After it lands, replay the trigger on the live board**: post the comment that was missed, build the context
   of the card that was bloated, and quote the output. Then remove the prose rule from step 4 that the mechanism
   made unnecessary, or shorten it to "the platform now does X; verify with Y". Rules that stay in two places
   drift.
7. **What you did not build is listed in the reply** with its cause and the card or issue that owns it, if any.

## Limits

- Do not waive or reword acceptance. Where a fixture predicate is stricter than the gate text, record your
  reading in the state file as overridable and tell the user in the reply.
- Sending as the user, activations and anything irreversible stay under the authorization already given. Writing
  an authorization into the body needs the user's own words to quote.
- Never replay a consumed approval, send or activation to make a number look better.

## Pitfalls

- `hermes kanban context` is what a worker sees; `show` is larger and misleads the size estimate.
- `promote` refuses until you acknowledge recent foreign comments: run it with `--dry-run`, take the `--seen <id>`
  it prints, rerun.
- For missing historical comment data, compare `promote --dry-run` with and without `--seen` once. Omitting
  `--seen` is not a guaranteed recovery: recent foreign comments can still require acknowledgment. If both
  refuse, stop alternating the commands; locate the existing comment-readback repair owner and verify its
  live run before handing off recovery. Reading comments alone does not prove acknowledgment succeeded.
- `edit --requires` replaces the capability list (`terminal,file,git`), not `runs_on`; placement is
  `move --pool` / `move --runs-on`.
- `wait-comment --contains` matches anywhere in a comment, so a note that quotes a grant/refuse token wakes the
  card waiting on it. Paraphrase tokens in your own comments.
- A card can move to `triage` under you (spend-budget hold). `spend-budget --runs N` counts runs already in the
  window (`spent: K`): grant K + new runs, or it re-holds at once.
- For `Downstream review implementer evidence is unavailable`, inspect concrete author evidence before promoting. A class-routed parent's launch may record only `class:standard` while its completed event records concrete `session_models`. When that evidence verifies the author, add the supported `<!-- implementer-models: model-id -->` declaration to the review body, validate it against the worker routing policy, preserve all review gates, then promote. Verify the new run's `model_tier.implementer_models`, independent `run_route`, and worker admission; promotion alone does not prove repair. Never invent author provenance or pin around the independence check.
- Use the CLI, not the sqlite file; the board database is not where the directory listing suggests.
- A guard hook may refuse `rm` on a variable path and `mkdir`/`git commit` it believes is in a live checkout: use
  literal absolute paths and `git -C <worktree>`.
- Filter large `hermes kanban list --json` results inside the command (`| python3 -c '…'`) before returning
  them to the tool; truncated captured JSON is unparsable. Extract bodies from the original stream.
- The gateway that answers `show` is not always the card's home: when `show` there prints no wake check but the
  events carry one with a `probe_host`, the card is homed on that host. Read the wake check, last probe result and
  your own edits back from the home (skill `hermes-workers` for pool workers).
- Before posting on a card that others wake on, read the wake checks of the cards waiting on it: a comment that
  happens to contain their match text wakes them. Say in the comment what it is not.
- Edit the card on its owning gateway. An edit on a replica is overwritten by the next sync.

## Done means
- [ ] The reply leads with age, run count, work hours and the bottom of the wait chain, each from a command?
- [ ] If the chain ends at the user, the reply states the exact decision and its choices before anything else?
- [ ] Every cause named has evidence, and the chain is written where the fix lives (state file or card comment)?
- [ ] State file exists, is under 8 KB, and the body tells runs to read only it?
- [ ] Old body saved; unlinked parents listed; `kanban context` size measured before and after?
- [ ] The card is in a status that can start (not triage, not waiting on a dead home), read back after the change?
      A cap you build routes the card to a linked replan card, not a bare park?
- [ ] At least one producer (skill, template, recipe) was edited, or the reply says exactly why none could be?
- [ ] Each platform defect claimed has evidence and a tested fix or named owner; mechanisms that worked are
      explicitly accepted without inventing additional implementation?
- [ ] Mechanisms not built are listed with their owner?
- [ ] Anything not done (a fix not written, an environment not built) is listed as not done?
