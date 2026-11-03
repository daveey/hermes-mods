---
name: recurring-job-state
description: "Use when a cron job or recurring card family needs memory. STATE.md."
version: 1.0.0
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

Prepend to the prompt: `STATE: first run python3 $S show <job> and follow its Lessons and Watch list.` Append:
`Last step, every run (including [SILENT] runs): python3 $S record <job> --last-run ... --tried ...` with the
fields above. Edit with `hermes cron edit <id> --prompt "$(cat new.txt)"` on the job's host, then trigger or wait
for one real run and diff the state file before/after.

## Done means
- [ ] The job's prompt (or card body) reads the state file before acting and records to it as the last step?
- [ ] The state file exists on the host that runs the job and has a Last run line from a real run after the change?
- [ ] Every new lesson has a `[slug]`, and each PROMOTE line was moved into the job's skill and marked `--promoted`?
- [ ] `python3 $S prune <job>` exits 0 (file within 150 lines)?
- [ ] Card family: a fresh card of the family shows the `job-state:` comment, and a finished run appears in Tried?
