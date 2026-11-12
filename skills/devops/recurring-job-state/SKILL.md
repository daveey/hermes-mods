---
name: recurring-job-state
description: "Use when a cron job or recurring card family needs memory. STATE.md."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [cron, kanban, state, lessons, recurring]
    related_skills: [kanban-orchestrator, kanban-worker]
---

# Recurring job state (STATE.md per job)

A recurring LLM job that starts from zero every run repeats its mistakes and pays to re-derive context each time.
Give it a small state file it reads first and updates last: `Last run / Tried / Lessons / Watch list`, capped at
150 lines, oldest Tried entries pruned by `scripts/job_state.py` (never by hand).

Not for: no-agent script crons (their own JSON state is enough), or jobs that already keep an equivalent log
(LOG.md + ledger, cron notepad, `context_from` previous output, an intervention log). Note those; don't add a second file.

## Script

`S=~/code/hermes-mods/skills/devops/recurring-job-state/scripts/job_state.py` (stdlib, any python3).
Files live in `~/.hermes/state/<job>.md` (`JOB_STATE_DIR` overrides; a path ending `.md` works too).

| Step | Command |
|---|---|
| Read first | `python3 $S show <job>` (creates the file if missing) |
| Update last | `python3 $S record <job> --last-run "<one line: result>" --tried "<what you tried + outcome>" [--lesson "[slug] rule"] [--watch "<thing to recheck next run>"] [--unwatch "<substr>"]` |
| Promote | `record` prints `PROMOTE [slug] xN` once a lesson slug recurs 3 times: add it to the job's skill as a yes/no `## Done means` line, then `record <job> --promoted <slug>` |
| Card family | one no-agent cron per board host runs `harvest <family> --db <kanban.db> --key-prefix <prefix>` every few minutes and comments `show <family> --slice` once on each new todo/ready/running card of the family (never on parked cards: a comment can satisfy a wait-comment probe). The worker returns `metadata.job_state = {"tried": "...", "lessons": ["[slug] ..."], "watch": [...], "unwatch": [...]}` on `kanban_complete`/`kanban_request_review`/`kanban_block`; workers on other machines never need the file. Lessons at 3x get one promotion card (idempotency key per slug) |
| Prune only | `python3 $S prune <job>` (exit 3 = still over cap: shorten Lessons/Watch list) |

Lesson slugs are short kebab-case names for the rule (`[gh-rate-limit]`), reused verbatim when the same lesson
recurs: the count is what triggers promotion. One line per entry; no prose paragraphs.

## Wiring a cron job

For family harvests, preserve the `harvest-v2` HTML-comment checkpoint in the file preamble.
It commits consumed run IDs and visible state in one atomic replacement under a lock; run IDs are not completion
order. Use `show --slice` for worker context so the checkpoint is omitted. A malformed legacy cursor fails closed.
Migration preserves legacy counts and tracks still-open lower-ID runs, but does not reconstruct historic omissions
hidden by the old high-water cursor. Never claim those recovered without separate source evidence.

Inventory actual generators on every requested host/board, including low-frequency ones. Dedupe counters are not
Tried/Lessons/Watch memory; finite project namespaces and human-action asks are not recurring repair families.
Validate each changed slice with an actual worker read and subsequent update, not merely a prompt or historic harvest.
A pool worker's transcript lives on that worker (`hermes-worker-N` in the run's `worker_bound` claim_lock), not
the hub: read it there (hub role + SSM) before calling a run prompt-only. Re-run `harvest` with every key prefix in
the slice's `harvest-v2` source (some families have two); a different prefix set fails `harvest source changed`.
For interruptible collectors, checkpoint each external delivery and reconcile its durable receipt before retrying;
test late completions, interruption and concurrent writers rather than only uninterrupted sequential runs.

When you change a cron's behaviour through its skill, read the cron's prompt too (`~/.hermes/cron/jobs.json`) and edit any
sentence that contradicts the change. The run obeys the prompt over the skill and will "fix" the skill, code and tests
back to the prompt's rule. A user-requested exception goes in the prompt and the state file's Lessons as well.

Prepend to the prompt: `STATE: first run python3 $S show <job> and follow its Lessons and Watch list.` Append:
`Last step, every run (including [SILENT] runs): python3 $S record <job> --last-run ... --tried ...` with the
fields above. Edit with `hermes cron edit <id> --prompt "$(cat new.txt)"` on the job's host, then trigger or wait
for one real run and diff the state file before/after.

## Fleet runs that reach a laptop

When the hub's fleet tick (the family harvest fan-out, or any cron that ssh's into each host) reaches a laptop,
a sleeping or closed laptop is *unavailable*, never a failed run. Otherwise every overnight sleep turns the cron
red and files a false defect that looks the same as a real outage. The rule, as in kanban-drift `monitor.py`
(`presence`, `absent_alert_after`) and the private family-state fleet script:
- ssh rc=255 with a connect-level error (`connect to host … Connection timed out|No route to host|Network is
  unreachable|Connection refused`, `Could not resolve hostname`, `Connection timed out during banner exchange`)
  records `unavailable_since` per host in the job's JSON state and exits 0. Capture stderr to classify it, then
  pass it through. Auth and host-key errors, the deadline (124) and collector rcs still fail at once.
- While the host is dark, a presence probe (`tailscale ping -c 1 --timeout 5s <host>`) splits it: if the host is
  absent, fail after 7 d; if it is up but ssh keeps failing, fail after 6 h. A probe that can't run counts as up,
  so a broken probe never hides a real failure. A flat 6 h window alone is not enough, because a laptop closed
  over a weekend trips it.
- Any real answer, success or a genuine failure, clears `unavailable_since`. Test a sleeping host, a long
  absence, an up host whose ssh fails, an auth failure, and clearing on success.

## Done means
- [ ] The job's prompt (or card body) reads the state file before acting and records to it as the last step?
- [ ] The state file exists on the host that runs the job and has a Last run line from a real run after the change?
- [ ] Every new lesson has a `[slug]`, and each PROMOTE line was moved into the job's skill and marked `--promoted`?
- [ ] `python3 $S prune <job>` exits 0 (file within 150 lines)?
- [ ] Card family: a fresh card of the family shows the `job-state:` comment, and a finished run appears in Tried?
- [ ] A fleet run that reaches a laptop exits 0 on a connect-level ssh failure (records `unavailable_since`) and fails only after the absent (7 d) or up-but-failing (6 h) window, with a test for each?
