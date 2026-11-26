# Notes

Reference for skill `github-ci-wait` (moved verbatim from SKILL.md).

- Auditing a workflow's history (green-window soak, "every run since D", promoting a lane to required): `references/workflow-history-audit.md`. Not `gh run list -L N`: on a busy workflow it drops the oldest runs.
- Writing or fixing CI itself (where a new script goes, path filters for guard workflows, unittest discovery,
  shard counts vs timeouts) and known failure classes (JS fixture socket paths, Hermes E2E daemon leaks, Docker
  gha cache export, gitleaks on self-hosted runners): `references/ci-authoring-and-failure-fixes.md`.

- On hermes-hub, bare `gh` is unauthenticated: a background `wait_for_ci.py` dies with `gh kept failing` ("gh auth login"). Wrap the whole script: `token-broker exec --scope user/github --reason '...' -- python3 -B $S <sha> ...`, and check it with `--once` first.
- When `gh` works but the script dies in its `git fetch` with `could not read Username for 'https://github.com'`, git has no credential helper: run it with `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0='!gh auth git-credential'` exported (it reaches the script's own git subprocesses; a `git -c` on your command line does not).
- When `gh` is only reachable through a broker wrapper (`token-broker exec ... -- gh`), never put a `gh -> wrapper` symlink first on PATH for `wait_for_ci.py`: the wrapper's inner `gh` resolves to the symlink again and recurses (dozens of `github_quota.py` guards, ledger `database is locked`, every call exits 75 `TimeoutExpired`). Use a shim script that removes its own dir from PATH before exec'ing the wrapper.
- A job cancelled with the check annotation `repository transferred` (`gh api repos/R/check-runs/<job>/annotations`) is the repo moving owner, not a flake or a code failure. Confirm with `gh api repos/OLD --jq .full_name`. After a transfer, self-hosted autoscalers bound to the old owner may stop registering runners, so a `rerun --failed` stays queued. Check `actions/runners` for online runners with the job's  labels before waiting again, and file an owner card for the fleet.
- An old PR with 0 check runs after a transfer did not necessarily skip CI: read `references/workflow-history-audit.md` §Pre-transfer CI history.
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
  head's run `pending`. Read back `status`/`conclusion` after a cancel before reporting it done (`ci_run_cancel.py`
  does, exit 4 = not settled); if stuck, re-run it with `--force`. When asked about one latched run, sweep the whole
  queue with `scripts/ci_latch_sweep.py OWNER/REPO` (dry run, then `--apply`): it selects only superseded-head runs
  whose open jobs are all gate/report jobs still waiting for a runner (a gate already running is left to finish), re-reads each run before cancelling, and never touches re-runs
  (`run_attempt>1`), runs with no jobs listed, or current-head runs; it exits 1 on any failed read or cancel. A `main` group
  without cancel-in-progress serializes, so a superseded main run latched on its timing report blocks the next one.
  Root fix: `if: ${{ !cancelled() }}` and run gate/report jobs on a light runner label; PRs pick it up only after
  rebasing, so keep the sweep running until then. Before adding concurrency rules, grep the workflows; they may
  already exist.
- When re-gating old PRs after a workflow change, neither a new `pull_request` run nor a refreshed merge ref containing the change proves selected workflow source. Close/reopen can still select the ORIGINAL PR-head workflow. Read the actual run's REST `node_id`, then GraphQL `node(id:$id){... on WorkflowRun{databaseId event runAttempt file{path repositoryFileUrl repositoryName} checkSuite{databaseId commit{oid}}}}`. Bind actual workflow URL, suite commit, run/attempt, complete jobs and exact-head check runs/app identity. Empty `referenced_workflows` does not supply this proof. If old workflow source lacks the required gate, have the authorized branch owner forward-port only the independently reviewed minimal CI delta to a NEW head (no routine whole-main catchup), obtain fresh exact-head independent review/all CI and prove genuine classifier/native/gate execution. A workflow/classifier edit may itself select native=true, so a skipped native job cannot qualify that control. Do not repeat close/reopen or synthesize statuses to preserve an immutable head; absent a supported path, keep it unqualified.
- For before/after Actions queue measurements, use JOB `started_at - created_at` consistently, not workflow creation time in one window and job creation in the other. Freeze the upper bound in the past using the live clock; a future query bound does not prove later coverage. Retain corrected queries within the SAME persisted request budget/cache, label non-atomic snapshots, cancelled assigned attempts and unequal-window/sample-size limitations, and never infer causal improvement from a few post-change runs.
- When `WorkflowRun.file.url` returns a generic `/actions/runs/<id>/workflow` endpoint rather than a commit-pinned blob URL, retrieve that exact page and inspect its “Workflow file for this run” link. It identifies the actual `/blob/<full SHA>/.github/workflows/...` source. Keep the page with run/head/suite/jobs/check-app evidence; the run title, REST `path`, and generic URL alone do not establish workflow-source identity. Public GitHub workflow pages can be read with `gh api --include https://github.com/OWNER/REPO/actions/runs/ID/workflow`; distinguish the displayed source link from unrelated commit/edit/navigation links.
- When the workflow owner authorizes a minimal CI-source forward-port for pre-rollout PRs, verify each original workflow blob matches the recipe's base and apply only its named paths; stop on drift. Preserve the production review ancestry, obtain an independent exact-new-head delta review, and require the actual new run's workflow/head/suite and selected native/gate jobs before enqueue. A close/reopen can retain old workflow source; local classifier tests or a successful classifier alone are not native gate qualification. Do not import unrelated main changes to repair workflow compatibility.
- Runs created during a GitHub Actions incident can stay `queued` with 0 jobs forever (cancel, force-cancel and
  rerun all refused): close and reopen the PR to refire its CI on the same sha, never an empty commit. Guards and
  branch pushes: `references/actions-outages.md` §Ghost runs. Before saying a fix waits on the outage, check that its
  landing needs CI (§Incident: a `state`-branch hermes-sync push goes live without it).
- A gate on the CURRENT head that sits `queued` is not a latch: it waits for a free runner with its labels. Read
  them first (`gh api repos/O/R/actions/jobs/<id> --jq .labels`; the id ends the check's `detailsUrl`), then
  diagnose that label's pool per `references/self-hosted-runner-migration.md` (Pitfalls, saturation by label).
- Tests: `python3 -B -m unittest discover -s tests` from this skill's dir.

Before Nix cache/save or stale fixed-output verification, read `references/nix-cache-verification.md`.

