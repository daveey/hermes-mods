# macOS state lane becomes a required check, with runner-loss recovery

Patches:

- `patches/1503-ci-require-selected-macOS-state-validation-and-recov.patch` (`4936cd1f0a`, `ci: require selected macOS state validation and recover runner loss`)
- `patches/1504-ci-use-explicit-portable-encodings-for-recovery-inpu.patch` (`b8a151be3b`, `ci: use explicit portable encodings for recovery inputs`)
- `patches/1505-ci-place-always-running-state-consumer-on-ci-fast.patch` (`508082a6d7`, `ci: place always-running state consumer on ci-fast`)

Earlier history of the lane: [0756 non-blocking lane](mirror-0756-ci-run-tests-hermes-state-on-a-self-hosted-macos-runner-non-blocking-lane.md), [0764](mirror-0764-fix-ci-let-macos-state-suite-finish-on-every-main-push.md), [0766](mirror-0766-fix-ci-declare-macos-runner-label-and-preserve-mktemp-errors.md). 1503 replaces 0756's "non-blocking, not called from ci.yaml" rule.

## Behavior

- **Selection.** `scripts/ci/classify_changes.py` adds a `hermes_state` lane. It is selected by changes to `hermes_state*` modules, `tests/hermes_state/`, the shared pytest harness (`_PY_TEST_HARNESS`) or the dependency manifests, and also by an empty diff, a `.github/` change, or a file list at the compare API's 300-file cap (a capped list may have dropped the state files). The `detect-changes` action exports it.
- **Calls from CI.** `ci.yaml` calls `hermes-state-macos.yml` (which gains `workflow_call` with a `commit` input) as job `hermes-state-macos` when the lane is selected and the event is not a fork PR. `detect` turns the lane off for a non-release push to `refs/heads/main`: the workflow's own `push` trigger already runs main, so the single mini runner never runs main twice. A release forces it on, and a call with an explicit `commit` input is not masked (the main-push check compares `inputs.commit || github.ref` with `refs/heads/main`). The called job also refuses fork PRs itself (defense in depth), so fork code never reaches the mini.
- **Required consumer.** A new, always-running job `macOS state required (v1)` (`needs: [detect, hermes-state-macos]`, `if: always()`) runs `required_results.py --macos-state`. If `detect` did not succeed, it fails: the selection is unknown. If the lane was selected (`'true'`), it needs `success`, so a skipped, failed, cancelled or missing lane fails it, and that includes a fork PR that touches the state surface. If the lane was explicitly not selected (`'false'`), `skipped` or `success` passes. Any other selection value fails. The `all-checks-pass` (v2) aggregate also lists `hermes-state-macos` as a selected-lane consumer. 1505 moves this consumer from the `hermes-hub` runner label to `ci-fast`.
- **Concurrency.** Standalone `push` runs share the group `hermes-state-macos-push-<ref>` without cancel-in-progress. A call from CI gets a group unique to its run id, attempt and commit, so a later standalone push never supersedes a required call.
- **Runner-loss recovery.** A new workflow, `hermes_state runner-loss recovery`, listens on `workflow_run: completed` for `CI` and `hermes_state (macOS)`. It reruns that run's failed jobs once (`POST .../rerun-failed-jobs`, which also reruns the aggregates that depend on them) only if all of the following hold:
  - the repository and head repository are the trusted fork;
  - the workflow path is approved;
  - the run is attempt 1, completed, conclusion `failure`;
  - there is exactly one macOS job with the approved name, it ran on the mini's named runner, it failed with no failed step, and every failure annotation on it is GitHub's exact "self-hosted runner lost communication" text;
  - every other non-successful job is a known downstream aggregate;
  - a PR run's PR is still open at the same head sha;
  - a re-read just before the POST still shows attempt 1 / completed / failure.

  Anything else is logged as a skip. Test failures carry a `Process completed with exit code N` annotation and a failed step, so they are never rerun.
- **1504.** `gh api` output is decoded as UTF-8 with `errors="replace"`, and the event file is read as `utf-8-sig`, so a BOM or non-UTF-8 locale does not break the decision.

## How it works

The recovery workflow is `permissions: {}` at the top, and the job gets only `actions: write`, `checks: read`, `contents: read`. It checks out `github.workflow_sha` with `persist-credentials: false`, never the failed run's head, and downloads no artifacts. It interpolates nothing from the event payload into a shell: `scripts/ci/recover_macos_runner.py --event "$GITHUB_EVENT_PATH"` re-reads every fact from the API through `gh api` with an argv (no shell). Its concurrency group is per failed-run id with cancel-in-progress off, so a duplicate event waits and then sees attempt 2. The job list and annotations are paged (100 per page, at most 50 pages). The run id must be a positive integer, and booleans and strings are refused. `--dry-run` decides without posting. A skip exits 0; an API error exits 1.

## Code

`.github/actions/detect-changes/action.yml`, `.github/workflows/ci.yaml`, `.github/workflows/hermes-state-macos.yml`, `.github/workflows/hermes-state-recovery.yml` (new), `scripts/ci/classify_changes.py`, `scripts/ci/recover_macos_runner.py` (new), `scripts/ci/required_results.py` (`evaluate_macos_state`, `--macos-state`).

## Tests

From 1503; **not run for this mirror**. No workflow was dispatched and no mini job ran.

- `tests/ci/test_macos_state_routing.py` (new, replays workflow YAML and expressions): `test_only_nonrelease_main_push_is_masked`, `test_release_forces_unselected_lane_and_explicit_commit_is_not_masked`, `test_classifier_action_detect_and_caller_chain` (paths × fork), `test_reusable_contract_commit_and_independent_concurrency`, `test_standalone_pushes_collapse_without_superseding_required_calls`, `test_required_consumer_executes_real_cli` (selection × result matrix through the real CLI), `test_unknown_detection_fails_closed`, `test_reusable_job_also_rejects_fork_prs`, `test_suite_launch_receipt_and_failure_propagation` (runs the step's shell with a `taskpolicy` shim: the argv, a throwaway `HERMES_HOME` removed on failure too, and exit-code propagation; this is not native macOS execution).
- `tests/ci/test_recover_macos_runner.py` (new): replays the historical runner-loss run (fixtures read back from a real run) and a historical test failure. It also covers the exact-message gate, the no-annotation case, event gates before any API call, a non-integer run id, an untrusted repository, a non-completed action, API state overriding a forged event, job gates, a duplicate job name, a CI reusable-call variant (a *documented* fixture, not a captured run), obsolete PR and unknown PR identity, any other failed job, pagination, re-read before mutation, a second event for the same run, dry run, `main` I/O and exit codes, the `gh` argv without a shell, and workflow-file checks (listened workflows, real job names, permissions and trusted checkout, no payload interpolation, concurrency, job prefilter).
- `tests/ci/test_classify_changes.py` (defaults include `hermes_state`), `tests/ci/test_required_results.py` (`hermes_state` → `hermes-state-macos` as a selected consumer).
- Untested: 1504's encoding change (no test added), the runner label added in 1505 is covered by 1519's `test_fork_rewriter_keeps_every_fast_job_on_the_fast_lane`. The ruleset requiring `macOS state required (v1)` by name is GitHub configuration outside the patch and is not verified here.

## Applying

Apply 1503 → 1504 → 1505 in the full ordered series (`git am -3 --empty=keep`, [README.md](README.md) Applying). They build on 0756/0764/0766 and the existing `required_results.py` and `classify_changes.py`. To use them in another repository, change `TRUSTED_REPOSITORY`, `RUNNER_NAME`, the runner labels and the `if:` repository check in the recovery workflow.

## Gotchas

- A fork PR that touches the state surface cannot pass `macOS state required (v1)`. That is deliberate: someone with access must land it from a same-repository branch.
- Recovery is attempted once per run (attempt 1 only). A second runner loss on the rerun needs a manual rerun.
- The runner-loss text is matched verbatim. If GitHub rewords it, every runner loss becomes a skip (fail-safe, but no auto-recovery).
- The required consumer and the recovery job need the `ci-fast` and `hermes-hub` self-hosted labels to have capacity. Neither job runs on the mini.

## Plugin?

No. These are the fork's CI workflows and scripts.


## Follow-ups 1516–1519

### Behavior

1516 repeats the selected macOS state lane/required consumer and bounded runner-loss recovery
change; 1517 specifies portable encodings; 1518 puts the always-running required consumer on
`ci-fast`. 1519 makes that fast-lane placement survive reapplication of `fork-ci-runner.sh` without
turning its fail-closed `always()` condition into `!cancelled()`.

- The `hermes_state` selector covers state modules/tests, shared Python test harness and dependency
  manifests. Empty diffs, workflow changes and compare results at the 300-file cap select it.
- Same-repository selected PRs, dispatches and releases run the reusable macOS lane. Ordinary main
  pushes mask the caller because the standalone workflow already runs main. Fork PRs never run on
  the self-hosted Mac, and a selected-but-skipped lane fails the required consumer.
- `macOS state required (v1)` runs regardless of predecessor outcome. Unknown/failed detection fails
  closed. Selected lanes require success; an explicitly false selector permits skipped or successful
  results, not arbitrary failures. This is separately required, not a completion job in the aggregate's
  needs. The ruleset must require its check name separately; the workflow edit alone is not proof of
  branch-protection rollout.
- Runner-loss recovery reruns failed jobs once only on first-attempt failures in the allowlisted
  repository/workflow/job/runner combination. Exact runner-loss annotations, no failed steps, no
  unrelated failed jobs, API reread before POST, and current open PR head checks are required.
  Assertion/test failures, superseded/closed PRs and repeat attempts do not qualify.

### How it works

`classify_changes.py` exposes the selector through the detect action. `required_results.py
--macos-state` evaluates the consumer. The reusable workflow's concurrency isolates required calls
from collapsed standalone main pushes. Recovery executes the trusted workflow revision rather than
the failed head, downloads no artifacts, and uses argv-based `gh api` with paginated job/annotation
reads and narrowly scoped permissions. 1517 decodes subprocess output as UTF-8 with replacement and
reads event JSON using `utf-8-sig`. 1519 adds `macos-state-required` to FAST, not COMPLETION.

### Code

- `.github/actions/detect-changes/action.yml`
- `.github/workflows/ci.yaml`, `hermes-state-macos.yml`, `hermes-state-recovery.yml`
- `scripts/ci/classify_changes.py`, `required_results.py`, `recover_macos_runner.py`
- `scripts/fork-ci-runner.sh`, `.github/FAST-LANE.md`

### Tests

Source coverage read from 1516–1519, not executed by this documentation agent:

- `tests/ci/test_classify_changes.py`, `test_required_results.py`: selector and required-result cases.
- `tests/ci/test_macos_state_routing.py`: caller/action/detect chain, main masking versus release,
  explicit commit/concurrency, fork refusal, actual consumer CLI and suite launch/failure propagation.
- `tests/ci/test_recover_macos_runner.py`: runner-loss-only decision, untrusted/forged API/event data,
  exact job/runner/annotation gates, failed-step refusal, pagination, duplicate attempts, PR freshness,
  dry run, argv client, workflow permissions and trusted checkout.
- `tests/ci/test_fast_runner_routing.py` (1519): `test_fast_label_is_known_and_checks_cannot_bypass_the_gate`
  treats this as a separately required consumer; `test_fork_rewriter_keeps_every_fast_job_on_the_fast_lane`
  runs the rewriter twice and verifies the consumer stays on ci-fast while keeping bare `always()`.
- 1517/1518 add no tests. No GitHub rerun was posted and no required-check ruleset was changed here.

### Applying

Use the complete ordered README replay recipe. 1516–1518 have the same feature subjects as 1503–1505
but different source SHAs. Their full diffs are byte-identical to 1503–1505. The public linear
series retains 1516–1518 as empty mail entries, because reapplying the earlier add-file diff
after the encoding/runner follow-ups conflicts. No source SHA or ordinal is dropped.

### Gotchas

Recovery is not a general flaky-test retry. The documented fast-lane runtime in the source is an
historical observation, not a benchmark rerun by this mirror. Trusted workflow checkout and exact
allowlists are security boundaries, not portability substitutions to loosen casually.

### Plugin?

No. These are source CI workflows, selection/recovery scripts and routing regression tests.
