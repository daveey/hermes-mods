# CI fast lane, 8-slice test runners, and the upstream-sync gate, 1203-1222

Patches: 1203 1204 1222 1206 1221

- `patches/1203-ci-upstream-sync-an-upstream-sync-reaches-main-only-.patch`
- `patches/1204-test-update-the-fork-sync-never-pushes-main-directly.patch`
- `patches/1222-ci-run-Python-tests-as-8-slices-on-one-VM-per-job-tf.patch`
- `patches/1206-ci-run-light-PR-checks-and-completion-aggregates-on-.patch`
- `patches/1221-fix-ci-resolve-upstream-sync-gh-under-service-PATH-d.patch`

Two themes: (a) an upstream sync reaches the fork's `main` only after a full suite passed on the fork's own runners (1203, 1204, 1221); (b) CI runner routing: Python tests on autoscaled one-VM-per-job runners, light PR checks on a bounded fast lane (1222, 1206).

## Behavior

- 1203: nothing pushes upstream commits straight to `main`. A gate script stages a candidate on `sync/upstream-<sha>`; a dedicated workflow runs the full suite there; the gate lands the candidate on `main` only if that run is green at exactly that sha. Landing is a fast-forward, else a non-forced merge commit of (`main`, tested head).
- 1203: `hermes update` with `updates.fork_merge_upstream` builds the merge off-tree, hands it to the gate, and does not move the live checkout to it. The strictly-behind push goes through the gate too. A checkout without `scripts/ci/upstream_sync.py` refuses ("no scripts/ci/upstream_sync.py gate") and pushes nothing.
- 1203: the pre-push hook (`scripts/check --commit`) refuses any branch push whose commits include `upstream/main` commits the fork's `main` lacks, unless the gate's env marker is set.
- 1204: test update only; asserts the updater never pushes `main`, even with push access.
- 1222: the Python test job runs as 8 slices on `[self-hosted, tfgar-tests]` (one on-demand VM per job), timeout 120 -> 30 min. The default `python_slices` is `[1..8]`; the PR affected-test planner's `MAX_SLICES` is 8.
- 1206: eleven short PR jobs move from `[self-hosted, hermes-hub]` to `[self-hosted, ci-fast]`, job names unchanged: `all-checks-pass`, `ci-timings`, `check-attribution`, `check-common-ancestor`, uv.lock check, icon freshness, package-lock diff, prompt-surface summary, and supply-chain `scan` / `dep-bounds` / `aggregate` (timeouts 15 -> 10 min, the lane cap). `scripts/fork-ci-runner.sh` pins every admitted fast-lane job back to `ci-fast`, so re-running it after an upstream merge cannot return them to the heavy pool.
- 1221: the gate resolves `gh` when run under a service `PATH` (checks `~/bin`, `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`); a missing `gh` falls through to the token-broker path, or ends in a clean `REFUSED` with no traceback.

## How it works

- Gate (`scripts/ci/upstream_sync.py`), exit codes `LANDED=0 REFUSED=1 NOT_YET=2`. `advance()`: fetch evidence; land any other green candidates; if `head` is already in `main` or merging adds no file (twin of a landed sync) report LANDED; no run yet -> stage; green -> `land()`; red -> REFUSED; pending -> NOT_YET.
- Evidence (`candidates()`): runs of `upstream-sync.yml` with `event=push`, where `head_branch == sync/upstream-<head_sha>` and `path` is that workflow. Newest run per head wins; cancelled/skipped/stale runs count as neither evidence nor failure. Green also requires (`_jobs_green`) every job in `REQUIRED_JOBS` (`Run tests (1/2)`, `Run tests (2/2)`, `e2e`) completed with `success`, a `self-hosted` label and a runner name. Unreadable or malformed evidence is REFUSED.
- Staging limits (`_stage_or_wait`): one candidate in CI at a time (a pending run older than 6 h stops counting); after a red run, 6 h backoff for candidates with the same upstream point (`merge-base` with `refs/remotes/upstream/main`).
- Publication (`land()`): `merge-tree --write-tree` + `commit-tree` when `main` moved; a conflict is REFUSED; a rejected push is NOT_YET; then it deletes the staging branch best-effort.
- The gate's own pushes set env `HERMES_UPSTREAM_SYNC_GATE=stage|publish`; `guard_push()` lets exactly those through. It refetches `origin/main` before refusing so a stale ref does not block.
- Workflow `.github/workflows/upstream-sync.yml`: trigger `push` on `sync/upstream-*`; concurrency group `upstream-sync-${{ github.run_id }}` with `cancel-in-progress: false` (main's busy-branch concurrency had cancelled the earlier runs); calls `tests.yml` with `python_slices: '[1, 2]'`, `e2e: true`, `e2e_upgrade: false`.
- `scripts/check` runs `_upstream_sync_refused()` for `--commit`. The hook runs the pushed tree's own `scripts/check`, so installed hooks pick it up without reinstalling.
- `_sync_fork_with_upstream(git_cmd, cwd, head="HEAD")` in `hermes_cli/update_cmd_git.py` runs the checkout's gate in a subprocess (900 s, no stdin, the resolved git's dir first on `PATH`) and returns True only on exit 0.
- 1222 (`tests.yml`): slices come from `run_tests_parallel.py` LPT balancing on `test_durations.json`, computed from the checkout alone. About 7.2 h of per-file wall time over 8 slices at `-j 16` (2 x nproc on 8 vCPU) targets ~7 min each. The `tfgar-tests` label is added to `.github/actionlint.yaml`. The runner group must exist before merge.
- 1206: `FAST-LANE.md` gains an evidence table (sample of 40 PR runs: 6-83 s jobs, queue up to ~50 min on the heavy pool). In `fork-ci-runner.sh` the inline Python splits `COMPLETION` (also gets `if: ${{ !cancelled() }}`) from `FAST` (label rewrite only), and rewrites `hermes-hub`, `hermes-hub-poll`, `ci-fast`, `ubuntu-latest` and `ubuntu-24.04` runs-on lines to `[self-hosted, ci-fast]`.

## Code

`scripts/ci/upstream_sync.py` (new; 1221 edits `github_pages`), `.github/workflows/upstream-sync.yml` (new), `hermes_cli/update_cmd_git.py` (`_sync_fork_with_upstream`, `_sync_with_upstream_if_needed`, conflict hint text), `scripts/check`, `.github/workflows/tests.yml`, `.github/actionlint.yaml`, `scripts/ci/affected_tests.py`, `.github/FAST-LANE.md`, `scripts/fork-ci-runner.sh`, and `runs-on` / timeouts in `ci.yaml`, `contributor-check.yml`, `history-check.yml`, `icons-freshness-check.yml`, `lockfile-diff.yml`, `prompt-surface-diff.yml`, `supply-chain-audit.yml`, `uv-lockfile-check.yml`.

## Tests

- 1203: `tests/scripts/ci/test_upstream_sync.py` (real bare remotes, fake Actions API, real pytest verdicts): `test_red_sync_is_refused_and_green_sync_lands`, `test_pending_run_does_not_land_then_lands_when_green`, `test_moved_main_gets_a_non_forced_merge_of_the_tested_head`, `test_incomplete_job_evidence_cannot_land`, `test_evidence_from_another_branch_or_cancelled_run_does_not_count`, `test_one_candidate_in_flight_and_red_backoff_per_upstream`, `test_another_hosts_green_candidate_is_landed_by_a_bare_call`, `test_unreadable_evidence_is_a_refusal`, `test_pre_push_guard_refuses_a_hand_pushed_sync_and_allows_fork_work`, `test_github_transport_reads_all_pages`.
- 1203: `tests/hermes_cli/test_update_fork_merge_upstream.py` (updater through the real gate subprocess, helper `tests/hermes_cli/upstream_gate_fake.py`): `test_flag_off_leaves_fork_untouched`, `test_flag_on_stages_the_merge_and_lands_only_a_green_one`, `test_conflict_moves_nothing`. Adjusted: `test_update_fetch_failure_classifier.py` (gate invoked with `--repo <cwd> --head HEAD`, no `pass_fds`, no direct push), `test_update_fork_sync_identity.py`.
- 1203: `tests/scripts/test_code_health_cli.py::test_pre_push_refuses_an_untested_upstream_sync_on_any_branch` (real installed hook; hand push to `main` and to a branch refused; gate `stage` env and fork-only work pass).
- 1204: `tests/hermes_cli/test_update_target_identity.py::test_branch_update_uses_real_refs_and_completion_request` now asserts no pushes and the "no gate" message.
- 1222: `tests/ci/test_fast_runner_routing.py::test_python_slices_match_their_matrix` (config assertions only: default slices, `MAX_SLICES = 8`, `tfgar-tests` label). No test shows a runner group exists or that slices finish in ~7 min (untested).
- 1206: `tests/ci/test_fast_runner_routing.py` (`FAST`/`COMPLETION` sets, `test_fork_rewriter_keeps_every_fast_job_on_the_fast_lane` running the real script twice, heavy jobs stay on `hermes-hub`) and `tests/ci/test_fork_runner_routing.py` (aggregators on `ci-fast`).
- 1221: `test_upstream_sync.py`: `test_github_transport_finds_home_bin_with_updater_path` (with and without the broker), `test_github_transport_missing_locally_can_use_broker`, `test_github_transport_missing_everywhere_refuses_cleanly`.
- Untested: `upstream-sync.yml` itself (no test runs the workflow or checks its YAML); the real `gh` API response shape (a fake is used).

## Applying

Apply the whole numbered series at the merge-base with `git am -3 --empty=keep`; patches are not independent. 1204 and 1221 only make sense after 1203 (1204 edits a test for the new gate behavior; 1221 patches `upstream_sync.py` and its test file). 1222 and 1206 both edit `tests/ci/test_fast_runner_routing.py`, so 1206 is written against 1222's result; 1206's `FAST-LANE.md` and runner-rewrite text assume earlier fast-lane patches in the series.

## Gotchas

- `REQUIRED_JOBS` hard-codes `Run tests (1/2)` / `(2/2)`; `upstream-sync.yml` pins `python_slices: '[1, 2]'`, so 1222's 8-slice default does not apply to the gate. Change both together if the gate should use 8.
- A candidate branch must be pushed only by the gate; evidence counts only from `sync/upstream-<sha>` runs of that workflow at that exact sha.
- The 6 h red backoff and one-candidate-in-flight rule mean a re-staged sync can answer NOT_YET with no new run.
- `tfgar-tests` runners must exist before merge, or the Python test job never starts (queues forever); the commit says the runner group was a prerequisite.
- 1206 moves jobs with a 10 min cap; a `ci-fast` job running longer will be cancelled. Heavy suites, the advisory `ruff + ty diff`, `label-rerun`, and secret-using release jobs deliberately stay on `hermes-hub`.
- The gate needs a GitHub `origin` URL (https or ssh `github.com`), else REFUSED.
