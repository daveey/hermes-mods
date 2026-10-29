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
| checks_failed | read the failing test names first. If main already fixed them (the PR's head predates the fix), `gh api -X PUT repos/O/R/pulls/N/update-branch -f expected_head_sha=<head>`, then diff base...new head: if the file list is unchanged, the existing review still applies, so hand the merge card the NEW head for `--match-head-commit`. Otherwise fix the PR. |
| checks_pending | schedule a retry; not a user ask |
| review_required / changes_requested | request or await a real review; address comments |
| blocked_unknown | diagnose (rulesets, required checks, queue); do not guess or ask |
| ready | attempt the normal merge (never `--admin`, never self-approve, never add bypass labels) |

`ready` still waits on unresolved review threads, which the script's `gh pr view` fields omit; a Graphite queue
label just sits on such a PR ("will be added once it meets the requirements"). List them:
`gh api graphql -f query='{repository(owner:"O",name:"R"){pullRequest(number:N){reviewThreads(first:50){nodes{id isResolved comments(first:1){nodes{author{login} body}}}}}}}'`.
The user requires each one fixed or refuted in its thread, bots included: reply with the
`addPullRequestReviewThreadReply` mutation, then `resolveReviewThread`. A fix push dismisses the approval, so
re-request the reviewer yourself (`gh api -X POST repos/O/R/pulls/N/requested_reviewers -f 'reviewers[]=<login>'`)
instead of blocking on it.

`checks_pending` with the new head's run stuck `pending` (no jobs) usually means an older run is holding the
workflow's concurrency group. Cancelled runs do this when an `if: always()` gate or report job
("All required checks pass", timing report, aggregate) still waits for a busy runner, and a main group
without cancel-in-progress serializes runs the same way. Read the workflow's `concurrency:` block before
proposing "add cancel-in-progress": it is usually already there. Release the latch by force-cancelling
(`gh api -X POST repos/O/R/actions/runs/<id>/force-cancel`) ONLY runs that meet all of these:
- the head is superseded (no longer the current PR head or main HEAD);
- the run has at least one job, and every open job is a gate or report job;
- `run_attempt` is 1 (a rerun may be a deliberate operator action; find its owner first).

For a main run you are timing, record created / first-job / last-real-job times before cancelling. `gh run cancel`
only *requests* cancellation: read back `completed/cancelled` before you count the run as cleared. The lasting fix
is to give gate jobs `if: ${{ !cancelled() }}` and run them on a light runner label. PR runs use the PR's own
workflow file, so open PRs keep the old behavior until they rebase.

Before asking the user anything, check live state again (the head may have changed, a rebase may have reset
approval) and put the proof in the ask. Do not repeat an ask a restart already made.

Admin override: only with explicit per-PR user authorization. Before offering it, read
`gh api repos/O/R/rulesets/<id> --jq .current_user_can_bypass`: `pull_requests_only` cannot skip a required
approving review (the merge is refused with "At least 1 approving review is required"), so the blocker is a real
review and asking for override authorization wastes a turn. Once a write-access approval lands, an authorized
`--admin` merge can still be needed for leftover policy (e.g. branch out of date).

Tests: `python3 -m pytest -q skills/devops/pr-blocker-check/tests`. Five-whys example:
`references/five-whys-stuck-approved-pr.md`.
