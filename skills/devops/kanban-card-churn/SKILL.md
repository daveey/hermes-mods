---
name: kanban-card-churn
description: "Use when one Kanban card has many runs, comments or a huge context and still isn't done."
version: 1.1.0
author: daveey
license: MIT
triggers:
  asks:
    - "why is this card taking so long and has so much context?"
    - "this card has 20 runs and 90 comments, clean it up"
    - "what can we learn from this card, and stop it happening again"
  not_for: "many red cards at once (use kanban-red-sweep); one failed run with a clear error (use five-whys)"
metadata:
  hermes:
    tags: [kanban, churn, context, cleanup, postmortem, card-design]
    related_skills: [five-whys, kanban-red-sweep, kanban-orchestrator]
---

# Kanban card churn: measure it, clean the card, fix what made it

A churned card is old, has many short runs, a long comment thread and a growing pile of files every run must
read, and its acceptance is still unmet. The card is rarely slow; it is waiting, re-reading and re-parking. This
skill has three parts and all three are required: **measure**, **clean up the card**, **change the producer** so
the next card does not end up the same way.

Answer the user's "why" in text as soon as step 2 is done, before any change. Then carry on.

## 0. Pick the card (when the user names none)

Rank open cards on the fleet replica by runs, then read the top ones. Skip standing cards and shepherds: they
are meant to recur.

```sh
sqlite3 "file:$HOME/.hermes/kanban/sync-shadow/default.db?mode=ro" "select t.id,t.status,count(r.id) runs,
 (select count(*) from task_comments c where c.task_id=t.id) comments,
 cast((strftime('%s','now')-t.created_at)/86400 as int) age_d, substr(t.title,1,70)
 from tasks t join task_runs r on r.task_id=t.id where t.status not in ('done','archived')
 group by t.id order by runs desc limit 15"
```
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
- **Run outcomes:** how many runs ended `blocked`/`scheduled` with the same gate unmet. Those are poll-and-repark
  runs.
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
| Resurrecting a failed run | days spent making a sealed or poisoned run pass | keep it as failed evidence; start a fresh run | body rule: fix forward, fresh run |
| Consumable authorization | "both sends used", then hours re-asking the user | quote the user's standing words in the state file | authorization and its scope in the body |
| Single-route dependency | one reviewer or model route quota-walled for days | name a fallback | named fallback in the body; never silent |
| Finished parents kept for the record | 10 to 20 done parents; `Parent task results` is half the context | unlink them, list ids in the state file | recipe: list finished prerequisites in the state file, do not link them |
| Other cards park on this one | consumer cards post a recheck comment every wake | state file rule: no recheck comments | fix the consumers' skill; check first whether it already says so |
| Workspace litter | hundreds of numbered scripts, several full checkouts | note it; delete only your own files | body rule: reuse one checkout, remove one-off scripts at run end |
| Poll-and-repark | runs that only reread state and reschedule | wake on the real producer event only | spend budget; wake checks on events, not timers |

## 3. Clean up the card

Before the first write, re-read the card's newest events and comments. Another chat or a budget gate may have
changed it while you were measuring; if their decision conflicts with yours, say so in a comment, do not silently
overwrite it.

1. **State file.** Write `<workspace>/STATE.md` (under 8 KB, overwritten, never appended). Sections: live version;
   gate table (gate, proof mode, status at this version, evidence link, what is left); root cause of the current
   failure; decisions; next action in order; standing authorization with the user's exact words, date and scope;
   reviewer or model routes with named fallbacks; never-replay list; where the frozen history is.
2. **Gate table instead of new cards.** Split acceptance into rows. Keep the original gate text word for word.
   Mark each row `fixture` or `real`. Do not open a card per gate unless the user asks for cards.
3. **Run the fixture now** if an isolated harness exists (often it was last run on an older version). Port it to
   the live version in a new directory, never edit the old run, and record PASS, FAIL, UNTESTED and harness errors
   separately. A harness that crashes is not a product failure.
4. **Body.** Save the old body to a file in the workspace first. If the body is the user's mandate (scope, limits,
   acceptance in his words), keep it verbatim and append a short HOW TO WORK block. Replace the body only when it
   is mostly procedure that caused the churn (for example an order to read a growing file every run). Either way
   it must say: read the state file first; gates close one at a time; fixture first; batch fixes; fix forward;
   never wait on a single-homed card; status goes in the state file. Keep the done rule ("block naming the exact
   gate, never done with a gate unproven").
5. **Parents.** Unlink parents that are done or archived (their results are a large share of the context) and
   list their ids in the state file. Leave open parents linked.
6. **Dead waits.** Do not unhold, restart or touch a held machine. Remove the dependency instead: record the
   failed attempt as evidence and make the next action something this card can do.
7. **Tell the thread once.** One comment: who you are, on whose instruction, what it supersedes, the files
   changed. Then read the status back. Promote or reschedule only a card that cannot start (triage, or a wake on
   a dead home). A card parked on a valid event wake with a backstop stays as it is.

Gate statuses you copied from run summaries rather than verified live are marked so in the state file, with the
run they came from; the owner corrects them on its next run.

## 4. Change the producer

A cleaned card whose producer is unchanged is a symptom fix. Find what wrote the card and what kept feeding it,
and change that in the same session:
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
- A card can move to `triage` under you (spend-budget hold). Check events before blaming your own edit.
- Use the CLI, not the sqlite file; the board database is not where the directory listing suggests.
- `unlink` takes several seconds per parent; run a long batch in the background.
- A guard hook may refuse `rm` on a variable path and `mkdir`/`git commit` it believes is in a live checkout: use
  literal absolute paths and `git -C <worktree>`.
- Extract a body with python reading `sys.stdin.buffer` and writing the file itself; a shell redirect of non-ASCII
  text can leave an empty file and a failed pipeline.
- Edit the card on its owning gateway. An edit on a replica is overwritten by the next sync.

## Done means
- [ ] The reply leads with age, run count, work hours and the bottom of the wait chain, each from a command?
- [ ] If the chain ends at the user, the reply states the exact decision and its choices before anything else?
- [ ] Every cause named has evidence, and the chain is written where the fix lives (state file or card comment)?
- [ ] State file exists, is under 8 KB, and the body tells runs to read only it?
- [ ] Old body saved; unlinked parents listed; `kanban context` size measured before and after?
- [ ] The card is in a status that can start (not triage, not waiting on a dead home), read back after the change?
- [ ] At least one producer (skill, template, recipe) was edited, or the reply says exactly why none could be?
- [ ] Anything not done (a fix not written, an environment not built) is listed as not done?
