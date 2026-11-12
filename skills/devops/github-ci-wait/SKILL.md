---
name: github-ci-wait
description: "Use when waiting for GitHub CI or a release."
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

For ordinary commit/release waits, use containment rather than "the newest run" (`gh run list -L 1`) or a single-run watch: branch concurrency can replace pending runs with a later run containing your commit. For input-specific experiments (native enabled versus skipped, RED/GREEN controls), containment alone is insufficient: bind evidence to the exact head, dispatch inputs, job and executed tests. A successful same-head run with the target job skipped cannot satisfy that experiment. See the acceptance recipe in `references/ci-authoring-and-failure-fixes.md`. For dispatched or experiment runs, record each run id from the dispatch output and wait with `wait_for_ci.py --run-id ID --expect ID:JOB=CONCLUSION` (below), never a hand-written waiter: one that filtered `gh run list` by a guessed `createdAt` matched none of five queued runs and exited 0 printing `{}`. Tag-mode success establishes containment only, not that CI passed
or release assets were published; verify the workflow and required release artifacts separately.

When a branch uses ONE serialized concurrency group (in-flight always finishes, only the newest pending waits), a
pending run that keeps getting `cancelled` with zero jobs may have been superseded before execution; corroborate
with concurrency configuration, cancellation evidence and replacement timing before classifying it. Find a possible holder: `gh api
"repos/R/actions/runs?branch=main&status=in_progress"` and look for an old run whose last job sat in a runner
backlog. To time a full run (capacity acceptance), designate the first run that STARTS after the holder completes,
and record both `created_at`→completed (wall, queue included) and `run_started_at`→completed (exec). A "CI finishes in
N min" target is judged on wall time unless the card says otherwise; exec time alone never closes it. A
`workflow_dispatch` on that branch joins the same group, so dispatching doesn't help.

Telling the user when a PR merges: green CI is one step of several; list them all from a check made that turn
(pr-blocker-check, "Telling the user when it merges").

Status of a finite LIST of runs: read `references/bulk-run-status.md` (bounded App-token inspector, never a `gh api` loop).

## Fixing the CI you wait on: use a worktree

A CI fix (or any branch work) goes in its own worktree: `git worktree add <dir> -b <fix-branch> origin/main`, then
push from there. Never `git checkout`/`switch` a branch in a shared checkout that something runs from (a sync
mirror, a deploy checkout, the live app checkout): the scripts and services in it vanish with the old branch.
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
python3 -B $S --run-id 41967431576 --expect 41967436628:native-guard=failure   # exact dispatched runs (below)
```

Exact-run mode (`--run-id ID`, `--expect ID:JOB=CONCLUSION`, both repeatable; ids in `--expect` join the list;
CONCLUSION is success|failure|skipped|cancelled): exit 0 only when every listed run completed and every expected job
ended with exactly that conclusion (a skipped job matches only an expected skip); a run with no `--expect` must end
`success`. Exit 1 at once with `no runs to wait on` for an empty manifest (also a blank `--run-id "$IDS"`), for a run id
gh can't find, a job that ended otherwise, or an expected job missing from a completed run. `--once` gives 2 while
anything is queued or running. Pass `--gh-repo OWNER/NAME` (dispatches often target helper branches of another repo).

Exit codes: 0 done (prints the run URLs or tags; a run that built a later commit containing yours is marked
`(ran on <sha>, a later commit containing it)`, so cite it as CI on that commit, not on yours. Such a run stands in
only when your commit has no run of that workflow of its own (none, or cancelled/skipped); otherwise your newest own
run decides, so a red own run is exit 1 even after a later commit's run passed (an older own run still queued or
running doesn't hold it at "not yet"): that run need not have run what
failed (plugin-suites picks suites by changed path). Clear it with `gh run rerun <id> --failed`, or wait on the commit
that fixed it. When a workflow's
newest finished run containing your commit went red after the green run that proved it (a later push broke it),
its entry ends `; but the newest finished run containing it, on <sha>, ended failure: <url>`: your commit passed,
but that workflow is red on the branch now. Never report the branch green from a pass line; read the notes); 1 stalled (nothing queued or running will finish it,
and the failing run's URL is printed; under `--once`, exit 1 also covers a failed job in a still-active run
when no other active run can finish the wait), or `gh run list` failed 5 times in a row (prints `gh kept failing`: a `gh`
auth or repo problem, not a CI failure); 2 timed out (default 120 min) or not yet with `--once` (a failed listing
included); 3 the sha isn't on `origin/<branch>` (default `main`; `--branch` to change). `--dispatch` (requires `--workflow`)
retries on a stall, at most twice, instead of exiting 1: a workflow whose own run on your commit went red gets that
run's failed jobs re-run (`gh run rerun <id> --failed`), because `gh workflow run` builds the branch head, and once the
branch has moved on, that later commit's run can't clear your commit's own red run. A workflow with no own run is
dispatched on the branch (`gh workflow run`), one that already passed is left alone, and a `--tag-glob` wait
dispatches every `--workflow` on the branch. Pass several shas to wait
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
  on the wait, put that step on a durable Kanban card with a `wake_check`, not a chat-owned chain. The wake_check is
  a small script around `--once` (fresh `token-broker exec` inside) that maps exits: 0 or 1 → `exit 0` (finished,
  green or red: wake), 2 → `exit 1` (not yet), else `exit 2`. Passing `--once`'s status through reads a red run as
  "not yet" and the card never wakes. `--check-every 900` keeps it under the shared GitHub budget.
- Never pipe the wait through a filter (`| tail -n`, a secret-scrubbing `| grep -v`): the pipeline's exit status is
  the filter's, so a failed or killed wait reports exit code 0 in its background notice, and
  `wait | filter && deploy` deploys after it. If you must filter, start the command with `set -o pipefail`; when
  one command chains several waits, keep each status (`a=$?`) and end with `exit $((a|b))`, not an `echo`. When a
  script chains steps after the wait, send the wait's output to a log file (`>> log 2>&1`) and test the wait's own
  exit code: tail also prints nothing until the wait exits, so a killed or hung wait leaves no record of what it
  was waiting on.
- A wait that ends with a wrapper's message instead of the script's verdict (e.g. a GitHub quota guard's
  `refused/failed`) was stopped by that wrapper, not by CI: run `--once` for CI's real state before reporting
  anything, then diagnose the wrapper. The hub's `github_quota.py` caps each process at a fixed request budget
  (exit 75, `Denied`), which one long wait outgrows; a slower `--interval` only delays it. For waits over ~20
  min, loop `--once` with a fresh `token-broker exec` per check (`sleep 300` between; stop on exit 0 or 1), so
  each check gets its own budget. Exit 75 with a `shared_quota_brake` ledger row: `references/quota-brake-recovery.md`.
- In a Kanban worker there is no async notify, and a poll loop burns `max_runtime_seconds` (most worker
  timeouts were exactly this). Run `--once`; if it exits 2, call `kanban_continue(checkpoint=<sha, what to
  check, next steps>, resume_after_seconds=600..1800)` and end the run. The card re-queues itself and the
  next run starts with `--once` again.
- Before telling the user or a card that something is "waiting on CI/the release", run `--once` with the same
  arguments. It has often shipped already, and a mistyped sha fails there at once (`Needed a single revision`); a
  background run reports that only in its exit notice, after you have announced the wait. Copy shas from `git`/`gh`
  output; never retype one.
- On exit 1, investigate the printed run (`gh run view <id> -R OWNER/REPO --log-failed`). Empty failed-step logs do not prove that a job never started or lost its runner. Check the command's exit status/stderr, run attempts, paginated jobs, full logs and annotations via each job's `check_run_url`; missing evidence is not a diagnosis. See `references/workflow-history-audit.md`. Attribute an earlier breaking commit only after verifying its diff and ancestry. On a branch already red, "inherited" means the same failing test ids, not the same workflow: list them per run (`gh run view <id> -R O/R --log-failed | grep -oE '(FAIL|ERROR): [A-Za-z0-9_]+ \([^)]*\)|(FAILED|ERROR) [^ ]+::[^ ]+' | sort -u`) and `comm -13` the previous run's list against this one's. A new id is a new break hiding under the old red (the last run without it and the first with it bracket the commits) and needs its own owner; a landing gate that skips a known-red workflow must diff ids the same way.
- If `gh run view --log` omits completed reusable-workflow jobs or `--job ID --log` returns empty with exit 0, fetch `gh api repos/OWNER/REPO/actions/jobs/ID/logs` directly before concluding logs are unavailable. A file-level all-pass summary plus the shard's `flaky_kanban: 0 flaky test id(s)` can prove first-attempt success when the runner does not print individual passing node IDs; verify the target exists in that run's source revision.
- Don't cancel another session's queued run to get yours through: the queued run will contain your
  commit too.
- Last: check Done means (below), fix at most 2 rounds, then report what still fails.

If failed-only rerun is refused, read `references/refused-failed-only-rerun.md` before diagnosing a broken workflow.

## Done means
- [ ] The sha was copied from `git`/`gh` output, and `--once` ran with the same arguments before any "waiting on CI"?
- [ ] Ordinary waits use `wait_for_ci.py`; input-specific controls additionally verify exact dispatch/job execution, not a skipped same-head success?
- [ ] No filter pipe hides the wait's exit status (`| tail`/`| grep` only under `set -o pipefail`)?
- [ ] The report cites the run URL or tag the script printed, marked "ran on a later commit" when it did?
- [ ] On exit 1, the failing run's logs were read (`--log-failed` or the jobs API) before a cause was named?

## Batch acceptance: several PRs, one suite

A user-approved batch through ONE full suite: acceptance is the batch's evidence only if one owner card builds it from fresh `main` with exact-head `--no-ff` merges, every workflow is green on the exact batch head, and `main` and the PR heads have not moved. Land with one fast-forward after `merge-base --is-ancestor`; never force-push `main` or fake statuses. Full checklist: `references/ci-authoring-and-failure-fixes.md` §Batch acceptance.

## Notes

- Auditing a workflow's history (green-window soak, "every run since D", promoting a lane to required): `references/workflow-history-audit.md`. Not `gh run list -L N`: on a busy workflow it drops the oldest runs.
- Writing or fixing CI itself (where a new script goes, path filters for guard workflows, unittest discovery,
  shard counts vs timeouts) and known failure classes (JS fixture socket paths, Hermes E2E daemon leaks, Docker
  gha cache export, gitleaks on self-hosted runners): `references/ci-authoring-and-failure-fixes.md`.

- On hermes-hub, bare `gh` is unauthenticated: a background `wait_for_ci.py` dies with `gh kept failing` ("gh auth login"). Wrap the whole script: `token-broker exec --scope user/github --reason '...' -- python3 -B $S <sha> ...`, and check it with `--once` first. The hub has no `~/code/hermes` checkout, so `--gh-repo daveey/hermes` alone exits 1 (`not a git checkout`): add `--repo ~/.hermes/repos/hermes-state` (branch `state`).
- When `gh` is only reachable through a broker wrapper (`token-broker exec ... -- gh`), never put a `gh -> wrapper` symlink first on PATH for `wait_for_ci.py`: the wrapper's inner `gh` resolves to the symlink again and recurses (dozens of `github_quota.py` guards, ledger `database is locked`, every call exits 75 `TimeoutExpired`). Use a shim script that removes its own dir from PATH before exec'ing the wrapper.
- A job cancelled with the check annotation `repository transferred` (`gh api repos/R/check-runs/<job>/annotations`) is the repo moving owner, not a flake or a code failure. Confirm with `gh api repos/OLD --jq .full_name`. After a transfer, self-hosted autoscalers bound to the old owner may stop registering runners, so a `rerun --failed` stays queued. Check `actions/runners` for online runners with the job's labels before waiting again, and file an owner card for the fleet.
- Wrap the broker in a shell function (`g(){ token-broker exec --scope user/github --reason "..." -- gh "$@"; }`), not an unquoted `$TB` variable: word-splitting the reason makes the broker exec a word from it as the command.
- In a fork checkout with an `upstream` remote, a bare `gh run`/`gh pr` reads the upstream repo's runs, so a
  hand-run one needs `-R OWNER/NAME` (the script passes it from `origin`; override with `--gh-repo`).

- With a narrow `remote.origin.fetch`, fetching a branch may update only FETCH_HEAD and leave its `origin/<branch>` tracking ref stale. The script now fetches `+refs/heads/<branch>:refs/remotes/origin/<branch>` explicitly; if an older copy reports “not on origin/…” after a verified push, compare `git ls-remote` and `git rev-parse origin/<branch>` and fetch that refspec yourself. Do not re-push an already verified head. Shas on different branches need one wait each (`--branch` per sha): several shas in one call must all be on that one branch.
- Pass `--repo /absolute/checkout --gh-repo OWNER/NAME --branch <actual-remote-branch>` when tracking helper branches. `--repo` selects the local git checkout; `--gh-repo` selects GitHub. Never use `--branch none` to disable filtering: it attempts to fetch a literal branch named `none` and fails before checking CI. Repair the wait arguments and reuse the existing runs; do not redispatch.

- Workflows with `paths:` filters don't run for every commit. Without `--workflow`, the script waits on the
  workflows that have a run on a commit containing yours. With `--workflow X`, when X never runs it reports a
  stall after the 3-minute startup grace period plus two idle polls.
- Own runs are found in the listing: the newest 100 runs on the branch, or the newest 40 per `--workflow`. On a
  busy branch, an older commit's own run can fall outside that window; it then reads as having none, and a later
  commit's run stands in. The verdict then tracks the branch as it is now, not your commit: `not yet` (rc=2) while
  newer pushes build, or a later red. To judge an old commit, read its own runs (`gh run list --commit <full 40-char sha>`: a short sha lists
  none, which reads like superseded). When
  it has none (superseded), cite each workflow's earliest finished run on a later commit that contains it
  (`git merge-base --is-ancestor <sha> <headSha>`).
- Runs triggered by `workflow_run`, `schedule` or `workflow_dispatch` (meta workflows such as "runner-loss
  recovery" and "CI review comment", which run on the branch head) say nothing about your commit. The script
  ignores these three events when discovering workflows and evaluating success, failure and activity; otherwise
  a meta-run (one PR comment's HTTP 502 in an hourly review-comment run) can delay or fail a green commit's wait.
  They still count when explicitly named with `--workflow` (a post-CI deploy; `--dispatch` always names it).
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
- Runs created during a GitHub Actions incident can stay `queued` with 0 jobs forever (cancel, force-cancel and
  rerun all refused): close and reopen the PR to refire its CI on the same sha, never an empty commit. Guards and
  branch pushes: `references/actions-outages.md` §Ghost runs. Before saying a fix waits on the outage, check that its
  landing needs CI (§Incident: a `state`-branch hermes-sync push goes live without it).
- A gate on the CURRENT head that sits `queued` is not a latch: it waits for a free runner with its labels. Read
  them first (`gh api repos/O/R/actions/jobs/<id> --jq .labels`; the id ends the check's `detailsUrl`), then
  diagnose that label's pool per `references/self-hosted-runner-migration.md` (Pitfalls, saturation by label).
- A repo with its own wrapper (e.g. a `tools/wait_for_release.py` that knows which product tags a path
  maps to) takes precedence; check the repo's AGENTS.md first.
- Tests: `python3 -B -m unittest discover -s tests` from this skill's dir.

## CI refused for billing (account-wide outage)

Every job fails in 1–4 s with zero steps across the account's repos, and the job's check-run annotation says it
was not started because payments failed or the spending limit was reached. It is not a code failure: don't
fix-forward or revert anything for it. Follow `references/actions-outages.md` §CI refused for billing: wait with
`scripts/actions_billing_probe.sh`, list who is waiting, notify only after the probe passes, then narrow the cause.

## Landing a reviewed PR with a head-pinned watcher

Order: exact-head review verdict AND **every required context** green, then
`gh pr merge N --rebase --match-head-commit <full sha>`. The watcher aborts on head move, any required-context failure or REJECT.
Never copy an aggregate display name from this skill into a manifest. Read the base branch's current protection
(`gh api repos/OWNER/REPO/branches/BASE/protection/required_status_checks`) and applicable repository rulesets;
record the exact context names and verify they are jobs on the pinned run. If policy cannot be read, use an
explicit independently validated manifest, not a guessed name or a success-only subset. Keep all required
contexts, including a separate platform-state gate. No prefix/version stripping, fuzzy fallback or missing-name
alias is safe: an absent required context remains failure. These job expectations are not a complete branch
protection/ruleset evaluator (nor app-identity validation); external status contexts need their own exact-head proof.
The probe checks only the `--expect` entries you pass: **the manifest must enumerate every required context**; a
required context you omit is silently unchecked (the probe is not a policy evaluator and cannot notice it). It
refuses a repeated `RUN:JOB` with conflicting conclusions before any gh call, and fails a run with more than one job
bearing an expected name (ambiguous, in any order); it never picks one by position.

For example, with a validated two-job policy, use the supported one-shot probe, not a custom waiter:
```bash
python3 -B "$S" --gh-repo OWNER/REPO --run-id "$RUN" --head-sha "$HEAD" --once \
  --expect "$RUN:All required checks pass (v2)=success" \
  --expect "$RUN:macOS state required (v1)=success"
```
`--head-sha` accepts a full lowercase 40-character SHA and requires every listed run's `headSha` to match;
it does not look up or pin the PR's current head. Re-read the PR head/state before and after proof and pin the
merge separately. A path-gated native lane's `skipped` conclusion is not that lane passing, nor a substitute for
any required gate. A successful policy gate can coexist with an optional native skip; report those distinctly.
See [required-context regression and causal chain](references/required-context-contract.md).
- Treat an empty or null `headRefOid` as a failed lookup and retry it, never as a head move: a broker or quota-guard refusal (`Denied`, a reserve brake) prints nothing to stdout, so `[[ "$head" != $pinned* ]]` fires on every PR at once and the watcher abandons them all. Poll at least 5 min apart, since each check costs core quota that other hub jobs share.
- Read a Kanban reviewer's verdict from the run, not the card: `select summary,metadata from task_runs where
  task_id=? order by id desc limit 1`. `tasks.result` is often empty, so a watcher grepping it never sees APPROVE and
  waits until timeout. Verify the verdict yourself before arming the merge.
- Every new head (your own rebase included) needs a fresh exact-head verdict, even when `git range-diff` shows
  every patch identical: main moved underneath it. Ask the SAME reviewer lineage for a delta review of the new
  sha (a done card can't be reopened unless it sits in review; create a continuation card, pinned to the same
  model, and link it as parent of the ship card).
- Exact-head failure: first read main HEAD's own failing checks (`gh api repos/O/R/commits/main/check-runs`) and their test ids: the same tests red on main mean every PR inherits them (shard numbers differ per PR), so find or file the main fix. Else check main's commits since the merge-base for a fix (rebase), or re-run the failing files on a detached worktree at the merge-base. Inherited: route it to the breaking commit's owner (search cards for the test name), rebase onto the fix, re-verify range-diff, push `--force-with-lease=<ref>:<old sha>`, request the delta review. Never merge over a red aggregate.
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
