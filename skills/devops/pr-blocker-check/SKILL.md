---
name: pr-blocker-check
description: "Use when checking PR blockers before waiting or merging. Classify the real blocker and name the next action."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [github, pr, merge, review, ci]
---

# Blocker-aware PR check (one shot)

Never write a watcher that only tests OPEN/MERGED/CLOSED, and never escalate on a bare `BLOCKED`.
Run the checker once per wake; schedule the next look with `kanban_continue(resume_after_seconds=...)`
(not a sleep loop or background script that dies on restart).

    python3 scripts/pr_blocker_check.py OWNER/REPO NUMBER      # wrap in the repo's gh credential vend

| verdict | action |
|---|---|
| merged | verify deployment, then the real end-to-end behavior |
| conflicting (DIRTY) | repair: rebase/merge base, resolve, push, re-check head |
| checks_failed | fix the named failing checks |
| checks_pending | schedule a retry; not a user ask |
| review_required / changes_requested | request or await a real review; address comments |
| blocked_unknown | diagnose (rulesets, required checks, queue); do not guess or ask |
| ready | attempt the normal merge (never `--admin`, never self-approve, never add bypass labels) |

Before asking the user anything, check live state again (the head may have changed, a rebase may have reset
approval) and put the proof in the ask. Do not repeat an ask a restart already made.

Admin override: only with explicit per-PR user authorization, and it can still be refused by repository
rules ("At least 1 approving review is required"); then the blocker is a real review.

Tests: `python3 -m pytest -q skills/devops/pr-blocker-check/tests`. Five-whys example:
`references/five-whys-stuck-approved-pr.md`.
