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
A failed or empty lookup (`gh` error, broker quota-guard `Denied`, null head) is *unknown*, never a verdict:
retry it, because reading it as "head moved" or "closed" abandons PRs that were about to merge.
Run the checker once per wake; schedule the next look with `kanban_continue(resume_after_seconds=...)`
(not a sleep loop or background script that dies on restart).

    python3 scripts/pr_blocker_check.py OWNER/REPO NUMBER      # wrap in the repo's gh credential vend

| verdict | action |
|---|---|
| merged | verify deployment, then the real end-to-end behavior |
| conflicting (DIRTY) | name the clash first, in a `git clone --shared --no-checkout` scratch clone (merge-tree writes objects) holding fresh `origin/main` and the PR head: `git merge-tree --write-tree --name-only --no-messages origin/main <head>` exits 1 and lists the conflicted paths below the tree oid, and `git log $(git merge-base origin/main <head>)..origin/main -- <paths>` names the landings behind them. Give both to whoever rebases (its owner card), then rebase, resolve, push, re-check head |
| checks_failed | read the failing test names first, then main HEAD's check-runs (`gh api repos/O/R/commits/main/check-runs`). Those prove only the suites that run executed: a runner that picks suites from changed paths (hermes-mods plugin-suites) stays green on main while main fails a suite it skipped, so grep main's run log for the failing suite, else run it at origin/main, before calling the red PR-specific. A job killed by its timeout (exit 124/137) names no failing test, and the test it died in is only a victim: diff per-file durations against that job's last green run to find what slowed. A runner that times out one named suite (`TIMEOUT <suite> (exit None)`, log stopping mid-dots) gets an onset matrix: loop `gh run list -w <workflow> -L 40 --json databaseId`, grep each `gh run view <id> --log` for `(PASS|TIMEOUT|FAIL)\s+<suite> `, and map the first TIMEOUT run to `headSha`/`headBranch`. If onset is a main commit and the PR doesn't touch that suite, main is the blocker (below): route it to one CI owner and park the PR on that fix instead of rerunning. A hang (job `cancelled` at its `timeout-minutes`, log stopped mid-suite) that main's own runs of that job also hit in the same hours, and that later main runs pass with no fix commit between, was environmental: `gh run rerun <id> --failed` once and note the attempt on the owner card; don't fix the PR. If main fails the same tests or job, main is the blocker: fix main (one card; first look for an open `Fix red <branch> CI` card and an open PR touching the slowed file, often from the culprit commit's own card) rather than patching each PR. Once main has the fix (the PR's head predates it), read the failing job's checkout step before moving any head. A `pull_request` job with no `ref:` tests GitHub's merge with the base as of the triggering event, so a re-run fails again; a new pull_request event tests the fixed main: the owner's next push, or, for an approved head nobody is changing, close and reopen, which keeps the head and so its exact-head approval (hermes-mods: hermes-mods-repo `references/mods-pr-landing.md`). Only a job that checks out the head itself needs the head moved: `gh api -X PUT repos/O/R/pulls/N/update-branch -f expected_head_sha=<head>`, then diff base...new head: if the file list is unchanged, hand the merge card the NEW head for `--match-head-commit` (an exact-head approval gate needs a fresh review of it). Otherwise fix the PR. |
| checks_pending | schedule a retry; not a user ask. A self-hosted job queued with no runner is not yet a runner outage: first read the newest run for the current head and `gh api "repos/O/R/commits?path=.github/workflows&per_page=5"`. The repo may have moved those jobs to other runners, leaving the queued job on an obsolete run. If main changed `runs-on` labels after the PR branched, the PR's own workflow file still targets the retired labels: rebase onto main and `--force-with-lease` push (check `git range-diff` shows all `=`), and hand the card the new head. To get a fix PR through a single scarce runner (e.g. one macOS box), cancel queued runs whose required checks already failed. Those PRs need a rebase anyway. |
| review_required / changes_requested | request or await a real review; address comments |
| blocked_unknown | diagnose (rulesets, required checks, queue); do not guess or ask |
| closed_unknown | CLOSED, mergedAt null, and the base-branch commit lookup failed: retry it, never read it as closed |
| ready | first check `git merge-base --is-ancestor origin/main <head>`. If the head lacks current main, its green CI never ran against main's newer changes: a stale branch that adds a CI job can pass and then go red on main. Run `update-branch` with `expected_head_sha`, confirm the diff against main is unchanged so the review still holds, wait for green CI on the new head, and merge with `--match-head-commit <new head>`. Re-read `mergeStateStatus` right before the merge and loop (update-branch → patch-id check → wait → merge) while it is `BEHIND`: on a busy repo main moves during the CI wait, and the merge is refused with the `--admin` hint, which is not an ask. Then attempt the normal merge (never `--admin`, never self-approve, never add bypass labels). A state-branch merge must run as the bare command with nothing after it: `gh pr merge N -R O/R --rebase --match-head-commit <FULL_SHA>`. ship-only-guard reads a trailing `2>&1` or `; next-cmd` as an extra arg and returns its generic "Land only through hermes-core-wt ship" deny. |

Graphite merge queue (e.g. softmax-universe/softmax) pushes a squash `… (#N)` to the base branch and closes the PR: state CLOSED, mergedAt null. The checker reports merged with the landed sha; by hand, look for the `(#N)` commit on the base branch near closedAt.

GitHub native merge queue: enqueue with `gh pr merge N --match-head-commit <sha>` and no method flag, because the queue sets the method. Then confirm the enqueue with `gh api graphql -f query='{repository(owner:"O",name:"R"){pullRequest(number:N){isInMergeQueue mergeQueueEntry{position state}}}}'`, since the command can exit 0 without enqueuing. Its most common cause is a required context that never reported, for example a workflow made required on main after the PR branched. The rollup's `isRequired` only marks contexts that ran, so "all required green" looks true. Compare the reported contexts with `gh api repos/O/R/branches/main/protection/required_status_checks -q '[.checks[].context]'`, then rebase so the new workflow runs. A queued PR that another landing has made obsolete (for example a quarantine entry the fix PR already removed) would undo that landing: take it out with the `dequeuePullRequest(input:{id:<PR node id>})` mutation before you close it.

`ready` still waits on unresolved review threads, which the script's `gh pr view` fields omit; a Graphite queue
label just sits on such a PR ("will be added once it meets the requirements"). List them:
`gh api graphql -f query='{repository(owner:"O",name:"R"){pullRequest(number:N){reviewThreads(first:50){nodes{id isResolved comments(first:1){nodes{author{login} body}}}}}}}'`.
Nits written in a review's body (no inline comment) open no thread, so that query misses them: also read
`gh api repos/O/R/pulls/N/reviews --jq '.[] | select(.body != "") | {id, state, body}'` and answer each in a PR
comment that quotes it. The user requires each one fixed or refuted in its thread, bots included: reply with the
`addPullRequestReviewThreadReply` mutation, then `resolveReviewThread`. A fix push dismisses the approval, so
re-request the reviewer yourself (`gh api -X POST repos/O/R/pulls/N/requested_reviewers -f 'reviewers[]=<login>'`)
instead of blocking on it.

daveey/hermes-hub lands only through push-guard's `land.py` (a bare `gh pr merge` there prints nothing and merges nothing): `python3 ~/code/hermes-mods/plugins/push-guard/land.py --repo daveey/hermes-hub --pr N --head <full sha> --review-task <approving review card> --implementation-task <author-model card>` under the GitHub credential vend. It refuses until every earlier review's non-blocking point is answered in ONE PR comment starting `<!-- hermes-review-reply -->` (fixed in <sha> / follow-up <card> / refuted) and every review thread is resolved; answering instead of pushing keeps the head, so the exact-head approval holds.

push-guard `ast.parse`s any Python it sees in a command (execute_code, `python3 - <<EOF`). A snippet that does not parse (e.g. a jq object `{…}` inside an f-string: `f"gh api … --jq '{sha: .sha}'"`) is held fail-closed with the raw-merge deny ("raw PR merges for a reviewed-landing repository … lack the gate"), even for read-only `gh api` reads. That deny is about syntax, not your merge: build jq filters as plain strings (or run `gh` from the terminal) and `ast.parse` the snippet before retrying.

An unanswered `hermes-ci-review-bot` "⚠️ Warnings" section on the PR's head (no later non-bot comment) blocks
every merge path, including a bare `gh pr merge`: fix it or refute it in a PR comment first (`hermes-core-wt ship`
enforces this; `--bot-reply "<why>"` posts the refutation).

Red required CI caused by main (the same tests fail at main without the PR) clears only on a new run against the fixed main (`checks_failed` row). For a core PR, once the
fix is on main, `hermes-core-wt ship` will NOT rebase by itself while origin `wt/<slug>` still equals the published
head ("resuming: ... not rebasing or re-gating", then review refused on the old red head). Run `git fetch origin
main && git rebase origin/main` in the worktree first, then rerun ship: it re-gates, pushes the new head and
reruns CI.

When Kanban completion reports `pr_acceptance: missing` although `gh pr checks --required` reports none, inspect `commits/<sha>/check-suites`, not just the PR rollup. Installed third-party apps can leave queued suites with zero runs. The acceptance gate treats these as potentially real CI until its idle-suite grace expires (check `hermes_cli/kanban_pr_fallback.py`; currently 15 minutes). Only after that can a documented `contract_waiver` cover genuine absent checks; it never bypasses active CI. Preserve the pushed deliverable and checkpoint until the grace elapses, then re-read exact-head checks and retry completion. Do not invent a success status or change repository CI to evade the gate.

A head with no Actions runs at all minutes after its push (`gh api 'repos/O/R/actions/runs?head_sha=<sha>' --jq .total_count` prints 0) while `mergeable` reads UNKNOWN is usually a conflict, not a queue: GitHub creates no `pull_request` runs for a PR that conflicts with its base. Run the conflicting row's merge-tree check before scheduling a retry. On a mergeable (CLEAN) PR, zero runs usually means no workflow covers its paths: list `gh api repos/O/R/actions/workflows --jq '.workflows[].path'` and read their `paths:` filters before gating a merge on "exact-head CI green". Otherwise the gate can never be met, and the PR lands on worker logs alone. Have the component owner add the missing workflow and gate later merges on it. For the same reason, a wake_check that parks on a PR head's CI must also wake (exit 0) when that head still has no runs ~15 min after its push: one that reads zero runs as "not yet" sleeps its owner until the park cap.

`checks_pending` with the new head's run stuck `pending` (no jobs) usually means an older run is holding the
workflow's concurrency group. Cancelled runs do this when an `if: always()` gate or report job
("All required checks pass", timing report, aggregate) still waits for a busy runner, and a main group
without cancel-in-progress serializes runs the same way. Read the workflow's `concurrency:` block before
proposing "add cancel-in-progress": it is usually already there. Release the latch by force-cancelling
(github-ci-wait `scripts/ci_run_cancel.py --run-id <id> --gh-repo O/R --expect-attempt 1 --force`; push-guard refuses a
bare `gh run cancel`/`…/force-cancel`) ONLY runs that meet all of these:
- the head is superseded (no longer the current PR head or main HEAD);
- the run has at least one job, and every open job is a gate or report job;
- `run_attempt` is 1 (a rerun may be a deliberate operator action; find its owner first).

For a main run you are timing, record created / first-job / last-real-job times before cancelling. A cancel
only *requests* cancellation (`ci_run_cancel.py` exit 4 = not settled yet): read back `completed/cancelled` before you count the run as cleared. The lasting fix
is to give gate jobs `if: ${{ !cancelled() }}` and run them on a light runner label. PR runs use the PR's own
workflow file, so open PRs keep the old behavior until they rebase.

Before asking the user anything, check live state again (the head may have changed, a rebase may have reset
approval) and put the proof in the ask. Before filing a review, hand-off or post-land card for a fix PR, and before
any merge, bypass or "no review is possible" ask, also list the other PRs for the same fix, open or merged since
it opened (`gh pr list --state all --search "<file or error text>"`; main's commits on the file:
`gh api "repos/O/R/commits?sha=main&path=<file>&since=<createdAt>"`), and read each open one's reviews and commit
statuses (`gh api repos/O/R/commits/<head>/statuses`): a second chat often opened, or already landed, the same fix.
A landed one makes this PR moot (verify that one instead and tell this PR's owner); an open one with an exact-head
approval or a `claude-code/reviewed-merge` status lands by its owner's own route, so the ask is moot. Agree with that owner which PR lands; the other then conflicts and keeps only its extra parts.

## Telling the user when it merges

Claim "approved" or "CI green" only for the SHA the approval or run names, and only when that SHA is the PR's current `headRefOid`. Being an ancestor of the head isn't enough, because later commits carry unreviewed code. Check with `gh run list -b <branch> --json headSha,conclusion`; a superseded run is `cancelled`.

Never tell the user when a PR will merge, land or deploy from a card's plan or an earlier reply. Check that PR in
the same turn (this checker, or `gh pr view N --json state,mergeStateStatus,reviewDecision` plus `gh pr checks N`)
and list every open step: failing or pending checks and whether main fails the same ones, whether the approval
still holds after a rebase (no review at the new head doesn't settle it: `hermes-core-wt ship`'s own clean rebase
carries the approval when the range-diff from the approved head is all `=`, and the owner card's ship result says
which; read that before reporting a re-review), the merge queue or slot position (core PRs: the land lane, patching-local-hermes-workflow `references/land-lane-stalls.md`; state PRs: other PRs' land-turn refs on its files, kanban-worker `references/state-pr-landing.md`), and the owner card's real state
(claimed and moving, or just ready). Give no clock time and no "merges once X" unless X is the only open step. CI is
rarely the only one: a hub merge slot or the land lane can hold a green PR for hours. A parked landing card's reason names
only the first blocker its run hit (the GitHub quota, say), so look for the rest yourself before promising "once X".
When X is a guard fix or config change, deploy X and run the gate's own check read-only before you promise it. For a
state carry, use the merge-proof probe in kanban-worker `references/state-pr-landing.md`. Reading the config back doesn't
show the gate's verdict.

Admin override: only with explicit per-PR user authorization. Before offering it, read
`gh api repos/O/R/rulesets/<id> --jq .current_user_can_bypass`: `pull_requests_only` cannot skip a required
approving review (the merge is refused with "At least 1 approving review is required"), so the blocker is a real
review and asking for override authorization wastes a turn. Once a write-access approval lands, an authorized
`--admin` merge can still be needed for leftover policy (e.g. branch out of date).

Tests: `python3 -m pytest -q skills/devops/pr-blocker-check/tests`. Five-whys example:
`references/five-whys-stuck-approved-pr.md`.
