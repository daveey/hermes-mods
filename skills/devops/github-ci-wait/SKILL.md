---
name: github-ci-wait
description: "Use when waiting for GitHub CI or a release. Track runs/tags containing the commit; diagnose Actions billing blocks."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [github, ci, actions, release, wait, background]
---

# Waiting for a commit's GitHub CI / release

## Rule

**Look first:** the exact sha from `git`/`gh` output, the repo's AGENTS.md (it may name its own wait wrapper), and `--once`.

Never write your own watcher that follows "the newest run" (`gh run list -L 1`) or one specific run id
(`gh run watch <id>`). On a busy branch, concurrency groups replace pending runs and most end `cancelled`.
The run that actually builds your commit is often one started by someone else's later push. Newest-run
watchers miss it and wait for hours while the session looks idle. Ask instead whether any successful run, or
any release tag, *contains* your commit. Tag-mode success establishes containment only, not that CI passed
or release assets were published; verify the workflow and required release artifacts separately.

When a branch uses ONE serialized concurrency group (in-flight always finishes, only the newest pending waits), a
pending run that keeps getting `cancelled` with zero jobs may have been superseded before execution; corroborate
with concurrency configuration, cancellation evidence and replacement timing before classifying it. Find a possible holder: `gh api
"repos/R/actions/runs?branch=main&status=in_progress"` and look for an old run whose last job sat in a runner
backlog. To time a full run (capacity acceptance), designate the first run that STARTS after the holder completes,
and record both `created_at`→completed (wall, queue included) and `run_started_at`→completed (exec). A "CI finishes in
N min" target is judged on wall time unless the card says otherwise; exec time alone never closes it. A
`workflow_dispatch` on that branch joins the same group, so dispatching doesn't help.

## Fixing the CI you wait on: use a worktree

A CI fix (or any branch work) goes in its own worktree: `git worktree add <dir> -b <fix-branch> origin/main`, then
push from there. Never `git checkout`/`switch` a branch in a shared checkout that something runs from (a sync
mirror, a deploy checkout, the live app checkout): the scripts and services in it vanish with the old branch. That
happened when CI-fix workers switched a sync mirror to fork branches and its launchd job failed for 4 hours.
[branch-pin-guard](../../../plugins/branch-pin-guard/) blocks this for checkouts listed in
`$HERMES_HOME/branch-pins.json`. A checkout you find off its branch: commit or stash the stray work onto its own
branch, check the pinned branch back out, and carry on in a worktree.

## Use the script

`scripts/wait_for_ci.py` (in this skill's dir). Run it from inside the repo checkout; it needs `git` and an authenticated `gh`.
From any other directory (e.g. `~`), `--gh-repo OWNER/NAME` makes it use that repo's `~/code/<name>` checkout (exact
`origin` match); with no such checkout it exits 1 at once, before any git or gh call.

```bash
S=<this skill dir>/scripts/wait_for_ci.py
python3 -B $S <sha>                                   # every workflow that ran on a commit containing <sha> passes
python3 -B $S <sha> --workflow ci.yml                 # just this workflow (repeatable)
python3 -B $S <sha> --workflow release.yml --tag-glob 'v*'   # released: a v* tag contains <sha>
python3 -B $S <sha> --once                            # one check: exit 0 done, 1 finished red, 2 not yet
```

Exit codes: 0 done (prints the run URLs or tags; a run that built a later commit containing yours is marked
`(ran on <sha>, a later commit containing it)`, so cite it as CI on that commit, not on yours); 1 stalled (nothing queued or running will finish it,
and the failing run's URL is printed), or `gh run list` failed 5 times in a row (prints `gh kept failing`: a `gh`
auth or repo problem, not a CI failure); 2 timed out (default 120 min) or not yet with `--once` (a failed listing
included); 3 the sha isn't on `origin/<branch>` (default `main`; `--branch` to change). `--dispatch` (requires `--workflow`) re-runs the
workflow via `gh workflow run` on a stall, at most twice, instead of exiting 1. Pass several shas to wait
until one run or tag contains all of them.

How to run it from an agent:
- Start it with `terminal(background=true, notify=true)` and end the turn. Don't run a foreground sleep loop,
  and bound every foreground ssh or wait with `timeout` well under the terminal's cap (420 s here; aim for ~200 s):
  the call holds the chat's turn until it returns, and messages the user sends meanwhile are refused ("session turn
  lease wait timed out"). Don't poll a background job either.
- A Hermes update or backend restart stops the chat's background job; it does not survive. On a core with
  91f48a91a20 (and 549b7f1e71b for a turn in flight) the chat is told, except in the cases below. If your turn was running, the
  resumed turn lists the job. If the chat was idle (you started the wait and ended the turn), the next
  backend starts a report turn whose note says "the Hermes backend shut down while background jobs from this
  chat were running". Each listing gives the job id, UTC start, command head and "restart it if you still need it",
  and a notify job also gets its completion notice ("killed by the Hermes update" or "killed when the
  Hermes backend shut down"). That notice means the wait was cut short, not that CI finished: run `--once`,
  then restart the wait if it's still needed. `persist_on_release` jobs are not stopped this way.
- When it's not covered: older cores; a backend that is SIGKILLed; auto-continue turned off
  (`desktop.auto_continue.enabled: false`); a message you type before the report turn starts; a receipt
  older than 24 h. In those cases the job can still die without a word. So after any update or restart, check
  each wait you promised with `process_manage poll` (or `--once`). If a deploy or a promised report depends
  on the wait, put that step on a durable Kanban card with a `wake_check`, not a chat-owned chain.
- Never pipe the wait through a filter (`| tail -n`, a secret-scrubbing `| grep -v`): the pipeline's exit status is
  the filter's, so a failed or killed wait reports exit code 0 in its background notice, and
  `wait | filter && deploy` deploys after it. If you must filter, start the command with `set -o pipefail`; when
  one command chains several waits, keep each status (`a=$?`) and end with `exit $((a|b))`, not an `echo`. When a
  script chains steps after the wait, send the wait's output to a log file (`>> log 2>&1`) and test the wait's own
  exit code: tail also prints nothing until the wait exits, so a killed or hung wait leaves no record of what it
  was waiting on.
- A wait that ends with a wrapper's message instead of the script's verdict (e.g. a GitHub quota guard's
  `refused/failed`) was stopped by that wrapper, not by CI: run `--once` for CI's real state before reporting
  anything, then diagnose the wrapper.
- In a Kanban worker there is no async notify, and a poll loop burns `max_runtime_seconds` (most worker
  timeouts were exactly this). Run `--once`; if it exits 2, call `kanban_continue(checkpoint=<sha, what to
  check, next steps>, resume_after_seconds=600..1800)` and end the run. The card re-queues itself and the
  next run starts with `--once` again.
- Before telling the user or a card that something is "waiting on CI/the release", run `--once` with the same
  arguments. It has often shipped already, and a mistyped sha fails there at once (`Needed a single revision`); a
  background run reports that only in its exit notice, after you have announced the wait. Copy shas from `git`/`gh`
  output; never retype one.
- On exit 1, investigate the printed run (`gh run view <id> -R OWNER/REPO --log-failed`). Empty failed-step logs do not prove that a job never started or lost its runner. Check the command's exit status/stderr, run attempts, paginated jobs, full logs and annotations via each job's `check_run_url`; missing evidence is not a diagnosis. See `references/workflow-history-audit.md`. Attribute an earlier breaking commit only after verifying its diff and ancestry.
- If `gh run view --log` omits completed reusable-workflow jobs or `--job ID --log` returns empty with exit 0, fetch `gh api repos/OWNER/REPO/actions/jobs/ID/logs` directly before concluding logs are unavailable. A file-level all-pass summary plus the shard's `flaky_kanban: 0 flaky test id(s)` can prove first-attempt success when the runner does not print individual passing node IDs; verify the target exists in that run's source revision.
- Don't cancel another session's queued run to get yours through: the queued run will contain your
  commit too.
- Last: check Done means (below), fix at most 2 rounds, then report what still fails.

## Done means
- [ ] The sha was copied from `git`/`gh` output, and `--once` ran with the same arguments before any "waiting on CI"?
- [ ] The wait is `wait_for_ci.py` (background+notify in chat; `--once` + `kanban_continue` in Kanban), never a newest-run or `gh run watch` loop?
- [ ] No filter pipe hides the wait's exit status (`| tail`/`| grep` only under `set -o pipefail`)?
- [ ] The report cites the run URL or tag the script printed, marked "ran on a later commit" when it did?
- [ ] On exit 1, the failing run's logs were read (`--log-failed` or the jobs API) before a cause was named?

## Batch acceptance: several PRs, one suite

When the user approves batching several PRs through ONE full suite, a constituent PR does not need its own green run.
Its acceptance is the batch's evidence, but only when all of the following hold:
- One owner card builds the batch branch from fresh `main` and merges each PR's exact head with `--no-ff`, so every
  source commit is an ancestor. That card records base sha, PR heads, integration head and tree, and run URLs in a
  manifest. Fix-ups the merged tree needs (lint, code-health) go on the batch branch as their own commits.
- Before pushing, run the repo's local lint gate (e.g. `scripts/check --base origin/main`) and focused tests. A
  ratchet that only fails on the combined diff costs a full suite otherwise.
- Every necessary workflow completed green on the exact batch head: one run per workflow, never "queued" or
  "in progress", never one job standing in for the others. A PR head or `main` that moves invalidates the
  evidence: rebuild and rerun.
- Land with one fast-forward of `main` to the tested head, after checking `git merge-base --is-ancestor
  origin/main <head>` immediately before the push. Never force-push `main`. Close each source PR only after the
  landing, with the batch PR link and main sha. Never fake check statuses on the source heads.
- Once the batch PR exists, cancel only the superseded runs of the constituent PRs. Leave runs on `main` and in
  other repos alone.
- The batch is done only when the post-landing `main` suite has completed green (`wait_for_ci.py <sha>`). Waiting
  cards get the run URLs so they don't relaunch suites. If that run fails in a test outside the batch diff and
  `main` has since fixed it, a later fully green `main` suite (every workflow) on a descendant of the landed sha
  counts; record the failed run, the fix commits and the green runs in the manifest.

## Notes

- Auditing a workflow's history (green-window soak, "every run since D", promoting a lane to required): `references/workflow-history-audit.md`. Not `gh run list -L N`: on a busy workflow it drops the oldest runs.
- Writing or fixing CI itself (where a new script goes, path filters for guard workflows, unittest discovery,
  shard counts vs timeouts) and known failure classes (JS fixture socket paths, Hermes E2E daemon leaks, Docker
  gha cache export, gitleaks on self-hosted runners): `references/ci-authoring-and-failure-fixes.md`.

- In a fork checkout with an `upstream` remote, a bare `gh run`/`gh pr` reads the upstream repo's runs, so a
  hand-run one needs `-R OWNER/NAME` (the script passes it from `origin`; override with `--gh-repo`).

- `--repo` is a LOCAL checkout path (the script runs `git -C <repo> fetch`). An `owner/name` slug resolves to `~/code/<name>` only when that checkout's origin is the same repo; anything else exits with "not a local checkout". Run it from inside the checkout or pass the path.

- Workflows with `paths:` filters don't run for every commit. Without `--workflow`, the script waits on the
  workflows that have a run on a commit containing yours. With `--workflow X`, when X never runs it reports a
  stall after the 3-minute startup grace period plus two idle polls.
- `workflow_run`-triggered workflows (for example, "runner-loss recovery" and CI review comments) carry the
  default branch's head, not the triggering run's commit. The script ignores these events when discovering
  workflows and evaluating success, failure and activity; otherwise another PR's meta-run can delay or fail a
  green commit's wait. They still count when explicitly named with `--workflow` (for example, a post-CI deploy).
  For other discovered workflows, runs whose every job is gated off by `if:` and ends `skipped` count as done
  once their runs on your sha have finished, and appear as `<name> skipped <url>`. With `--workflow X`, a
  skipped X is still not a pass.
- A cancelled run can stay `queued` indefinitely: an aggregator job with `if: always()` ("All required checks
  pass") still needs a runner, and on busy self-hosted runners it holds the PR's concurrency group, leaving the new
  head's run `pending`. Read back `status`/`conclusion` after `gh run cancel` before reporting it done; if stuck,
  `gh api -X POST repos/O/R/actions/runs/<id>/force-cancel`. When asked about one latched run, sweep the whole
  queue with `scripts/ci_latch_sweep.py OWNER/REPO` (dry run, then `--apply`): it selects only superseded-head runs
  whose open jobs are all gate/report jobs still waiting for a runner (a gate already running is left to finish), re-reads each run before cancelling, and never touches re-runs
  (`run_attempt>1`), runs with no jobs listed, or current-head runs; it exits 1 on any failed read or cancel. A `main` group
  without cancel-in-progress serializes, so a superseded main run latched on its timing report blocks the next one.
  Root fix: `if: ${{ !cancelled() }}` and run gate/report jobs on a light runner label; PRs pick it up only after
  rebasing, so keep the sweep running until then. Before adding concurrency rules, grep the workflows; they may
  already exist.
- A gate on the CURRENT head that sits `queued` is not a latch: it waits for a free runner with its labels. Read
  them first (`gh api repos/O/R/actions/jobs/<id> --jq .labels`; the id ends the check's `detailsUrl`), then
  diagnose that label's pool per `references/self-hosted-runner-migration.md` (Pitfalls, saturation by label).
- A repo with its own wrapper (e.g. a `tools/wait_for_release.py` that knows which product tags a path
  maps to) takes precedence; check the repo's AGENTS.md first.
- Tests: `python3 -B -m unittest discover -s tests` from this skill's dir.

## CI refused for billing (account-wide outage)

Symptom: every job fails in 1–4 s with zero steps, across all workflows and repos of the account. The cause is
in the job's check-run annotation, not the log: `gh api repos/R/check-runs/<job id>/annotations --jq '.[].message'`
contains "The job was not started because recent account payments have failed or your spending limit needs to
be increased". This is not a code failure: don't fix-forward or revert anything for it, and tell running repair
workers to stop chasing tests.

1. **Wait for it to clear with this skill's `scripts/actions_billing_probe.sh OWNER/REPO [cheap-workflow.yml]`**
   (don't write a new probe script: this one already handles the in-progress and zero-steps cases), started in
   the background with notify on `ACTIONS OK` / `STILL BLOCKED`. The probe re-runs a cheap Linux workflow, so it
   doesn't depend on someone pushing. "I raised the limit" isn't proof: confirm with the probe before relaying it.
   For a one-shot "are we still blocked?", don't scan recent runs: once repos are on self-hosted runners, no
   hosted job may have run for hours, so recent runs prove nothing. Re-run the refused job itself
   (`gh run rerun <run id> -R O/R --failed`, ~60 s, reuses upstream job outputs) and read its annotation.
2. **List everyone who is waiting before it clears**, and save the list to a scratch file so it survives compaction:
   - chats: `session_search` on the exact phrases `"payments have failed"` and `"spending limit"`. One search
     misses sessions that only quoted the other phrase.
   - Kanban: read the *latest* `blocked` event reason of each blocked card, not the title:
     `select t.id,t.session_id,json_extract(e.payload,'$.reason') from tasks t join task_events e on e.task_id=t.id where t.status='blocked' and e.kind='blocked' and e.created_at=(select max(created_at) from task_events where task_id=t.id and kind='blocked') and (e.payload like '%spending limit%' or e.payload like '%payments have failed%' or e.payload like '%billing%')`.
     Run it on every board/host that has workers, e.g. a remote host's board db over ssh.
   - The card `session_id`s add the chats that own those cards.
3. **Notify only after the probe passes.** A message arrives as a user turn, so sending it early makes every
   session retry into the same refusal. Once it passes: `cross_session send` each chat (CI is back, resume),
   then `kanban_comment` + `kanban_unblock` each card (or `hermes kanban --board <b> comment/unblock` on the
   remote board).
4. **If the probe keeps failing more than ~30 min after the user's change,** tell the user what you observed (how
   many probes, first and last times), then narrow the cause yourself before handing them pages to click:
   - Scope: list the account's repos' newest runs. Public repos still running while private ones are refused
     means GitHub is blocking all *paid* usage on the account, not one workflow or repo.
   - Usage: `gh api "/users/<user>/settings/billing/usage?year=Y&month=M"` returns per-day, per-repo, per-SKU
     items: aggregate `grossAmount` by (repo, sku) and by day to find the runaway workflow (hosted macOS is the
     usual one). There's no API for a personal account's budgets (`/settings/billing/budgets` is 404): read the
     Budgets, Payment information and Payment history pages yourself with `browser_exec(local=true)` (the user's
     signed-in Chrome) instead of asking for screenshots.
   - Mail: search the user's Gmail (same browser) for GitHub mail since the block began (budget reached,
     payment failed, `[GitHub Support]`). No such mail plus a budget under its cap plus all-successful payments
     points away from budget/charges.
   - "The issue we filed" about billing usually means a GitHub Support ticket, not a Kanban/board card: find its
     id in the `[GitHub Support]` mail and read it at `https://support.github.com/ticket/personal/0/<id>` before
     answering. Check board cards only if no ticket exists.
   - Then the remaining documented cause is a failed card **authorization hold**: GitHub places holds after
     significant metered usage or a budget increase, a declined hold locks billing, and it never appears in
     payment history (that only lists completed charges). Docs:
     https://docs.github.com/en/billing/how-tos/troubleshooting/locked-account. Tell the user to look for a
     declined GitHub authorization at the bank, re-save the card on the Payment information page (that triggers a
     new authorization), and otherwise open a support ticket asking for a billing-lock review.
   - Keep the probe running throughout; it ends the wait the moment any of these clears it.
5. **When billing can't be fixed soon, move repos to self-hosted runners instead of waiting** (registration,
   no-sudo workflow edits, dirty-checkout pitfalls, refused PR reruns): `references/self-hosted-runner-migration.md`.
   Before moving a repo, check its `runs-on` and runners: another session may have moved it already.
6. **Detection:** `~/.hermes/scripts/gh_actions_billing_watch.py` (hourly no_agent hub cron) files verified Kanban cards for refused jobs, spend thresholds, and persistent scan failures. Scan-unavailable errors are operational evidence, not confirmed billing locks: inspect broker vend failure_phase/failure_class, blocked reason, attempts and samples before choosing remediation. Never blindly retry exit75, which also represents quota denial. For read-only live verification, import and call `refused_jobs(now)` and `spend(now)` directly; `main()` and `--force` can create real cards, so tests must isolate the card sink and state.

## Landing a reviewed PR with a head-pinned watcher

Order: exact-head review verdict AND exact-head required aggregate ("All required checks pass") green, then
`gh pr merge N --rebase --match-head-commit <full sha>`. The watcher aborts on head move, aggregate failure or REJECT.
- Read a Kanban reviewer's verdict from the run, not the card: `select summary,metadata from task_runs where
  task_id=? order by id desc limit 1`. `tasks.result` is often empty, so a watcher grepping it never sees APPROVE and
  waits until timeout. Verify the verdict yourself before arming the merge.
- Every new head (your own rebase included) needs a fresh exact-head verdict, even when `git range-diff` shows
  every patch identical: main moved underneath it. Ask the SAME reviewer lineage for a delta review of the new
  sha (a done card can't be reopened unless it sits in review; create a continuation card, pinned to the same
  model, and link it as parent of the ship card).
- Exact-head failure: first list main's commits since the merge-base
  (`gh api repos/O/R/compare/<head>...main --jq '.commits[].commit.message'`); one that fixes the failing test
  means it is inherited and already fixed, so rebase. Otherwise re-run only the failing test files on a detached
  worktree at the PR's merge-base. Failing there too means it is inherited; route it to the owner of the breaking commit (search cards and comments for
  the test name first), wait for that fix to merge, rebase onto it, re-verify range-diff, push with
  `--force-with-lease=<ref>:<old sha>`, then request the delta review. Never merge over a red aggregate.
- Hosted Actions refused for billing on a repo WITHOUT branch protection or required checks: run the workflow's
  exact commands locally at the exact head for every matrix entry (`uv run --no-project --python 3.X --with
  pytest python -m pytest ...`), run any tests a worker's mutation fence blocked as the supervisor in a clean
  env (`env -i HOME=<tmp> PATH=/usr/bin:/bin ...`), post the evidence on the PR, then merge with
  `--match-head-commit`. Never do this on a repo whose protection requires the checks.

## Rescuing a stalled merge watch

For an open PR, inspect blockers before restarting a watcher:
`gh pr view N --repo OWNER/REPO --json state,headRefOid,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,autoMergeRequest`,
plus unresolved review threads, which that JSON omits (query in pr-blocker-check).
A loop checking only OPEN/MERGED/CLOSED cannot detect conflicts or failed checks; approval and auto-merge are not
proof of progress. If the watcher repeatedly dies on restarts, move follow-through to a durable task with scheduled
one-shot checks, not another long-lived shell loop.

- `DIRTY`/`CONFLICTING`: fetch the base and reproduce with `git merge-tree --write-tree HEAD origin/main` in the
  PR worktree. Resolve both sides' changes, test, and push; verify GitHub now reports `MERGEABLE`. Re-read review
  and check status on the new head—rebasing can invalidate approval. Never present conflict repair as deployment.
- Generic `BLOCKED`: inspect required checks and review rules; do not infer a specific policy failure or propose
  an admin override from that label alone. A refusal of an override does not prohibit fixing conflicts normally.
- For a CI/release watcher following the newest run or one run id (`ps -axo pid,etime,command | grep 'gh run'`),
  check the commit with `--once` instead. If already shipped, stop the obsolete watcher: a `zsh -lic` loop ignores
  SIGTERM, so use `pkill -P <pid>; kill -9 <pid>` and confirm with `ps -p`. Report the containing run/tag to the
  session and tell it to use this script next time.

## Kanban card parked on an external PR

Use the runtime's read-only terminal-state probe as the `wake_check`, not a `gh ... --jq .state` test (that
treats API failures as pending and never wakes for a superseded PR). Graphite merge queues close the
original PRs they land: CLOSED, never MERGED. The `kanban-pr-wake-guard` plugin now refuses raw gh PR
state/merge wake checks on `kanban_block` and `kanban_schedule`, and warns on non-terminal probes such as
`reviewDecision` that may never become true after closure. Use:
`hermes --run-module hermes_cli.kanban_pr_wake https://github.com/OWNER/REPO/pull/N`. Exit 1 = OPEN, 0 = merged
or closed-unmerged (wake to reconcile; neither is acceptance), 2 = evidence/auth error, never pending.
- When the change will land through a replacement commit while the original PR stays OPEN (a squash train, a
  batch PR, a cherry-pick), pass BOTH anchors: `--superseding-commit <full 40-char sha> --verification-job
  <Actions job id that succeeded on exactly that sha>`. It wakes only once that sha is contained in the default
  branch and that job succeeded on it. Passing one flag alone is an error by design. The caller names the
  replacement; the probe never infers equivalence, and one job is not all required CI.
- On wake, reconcile: verify the replacement's behavior, owner dependencies and exact-head required CI before
  claiming delivery. CLOSED-unmerged alone never means delivered.
- Stored probes aren't rewritten automatically. When re-parking a card that still has an old `gh` state probe,
  switch it to this one.
- This is not a CI-ready probe: an OPEN PR with green checks still waits. Use an exact-head CI probe when the next
  step is review/landing.
