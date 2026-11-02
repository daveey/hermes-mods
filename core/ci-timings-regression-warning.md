# CI timings: warn on a wall-time regression only when the PR's own tests explain it

Patch: `patches/1582-daveey-CI-timings-warn-only-when-the-PR-s-own-tests-.patch` (`673f6c936b`, `CI timings: warn only when the PR's own tests explain the wall-time regression`).

Related: [TUI CI timing stability](tui-ci-timing-stability.md).

## Behavior

- The CI-timings review item used to raise a Warning whenever a PR's CI wall time was more than 25% above main's. On one day, three PRs were warned for +34–44% while the tests they added took 9–19 s. Per-shard deltas also went both ways (+183 s on one shard, −110 s on another): the slowdown was CI-wide. Those warnings went unanswered.
- Now a regression stays a **Warning** only if the PR's added/changed test files took **≥ 60 s** in total in this run, or one test case the PR **added** took **≥ 10 s**. The summary names the files and cases with their seconds.
- Otherwise the item drops to **Info**: `CI wall time +N% vs main; not attributable to this PR (its added/changed test files took S s)`.
- With no PR data (a push run, or the files API failed), the old Warning stands.
- The detail lists every added/changed test file with its seconds, marking files that did not run as `(not run)`.
- The test runner prints `  ⏱ slow test case <s>s <node id>` for each case whose setup + call + teardown is ≥ `HERMES_TEST_SLOW_CASE_S` (default 10; `0` disables).

## How it works

- `scripts/run_tests_parallel.py` appends `--durations=0 --durations-min=<threshold/4>` to each per-file pytest run, unless the threshold is 0 or the caller passed its own `--durations`. `_slow_cases_from_output` adds up the phases from pytest's "slowest durations" section.
- `scripts/run_tests.sh` passes `HERMES_TEST_SLOW_CASE_S` through its stripped environment.
- `scripts/ci/timings_report.py`:
  - `_collect_cache_data` already downloads each job log. For jobs named `Run tests (...)` it now also runs `parse_test_durations`, which reads the per-file progress lines (`✓|✗ <file> (..., Ns)`) and the slow-case lines, keeping the maximum per key.
  - `collect_pr_changes` takes the PR number from `GITHUB_EVENT_PATH` or `refs/pull/<n>/`. It reads `/pulls/<n>/files` (`per_page=100`) and keeps the non-removed test files, plus the `def test…` names that each patch adds.
  - `pr_test_attribution` computes the totals. `generate_review_status` downgrades or rewords only when the severity was already `warning`.
- `.github/workflows/ci.yaml`: the `ci-timings` job gets `pull-requests: read`.

## Code

`scripts/ci/timings_report.py`, `scripts/run_tests_parallel.py`, `scripts/run_tests.sh`, `.github/workflows/ci.yaml`.

## Tests

In the patch; not run for this mirror. The source commit names both files.

- `tests/ci/test_timings_attribution.py`:
  - `test_ci_wide_slowdown_with_small_pr_files_is_info`
  - `test_pr_files_totalling_60s_or_more_warn_and_name_the_files`
  - `test_one_added_case_of_10s_or_more_warns_and_names_it`
  - `test_slow_case_that_the_pr_did_not_add_stays_info`
  - `test_without_pr_data_the_regression_still_warns`
  - `test_review_status_only_reads_pr_attribution_from_the_saved_json`
  - `test_parse_test_durations_reads_progress_and_slow_case_lines`
  - `test_collect_pr_changes_lists_test_files_and_added_cases`
  - `test_collect_pr_changes_outside_a_pull_request_is_none`
- `tests/scripts/test_run_tests_parallel_slow_cases.py`:
  - `test_slow_cases_sum_phases_and_keep_only_cases_over_the_threshold`
  - `test_durations_flags_are_added_unless_disabled_or_given`
  - `test_runner_prints_the_slow_case_line`
- Untested: a live GitHub files-API call, and a PR with more than 100 files.

## Applying

Depends on the preceding series (the existing timings report, cache-log parsing and parallel runner). Apply the full ordered series per [README.md](README.md) "Applying". Standalone application is not certified. The workflow permission change only takes effect once `ci.yaml` is on the default branch.

## Gotchas

- Only the first 100 changed files are read; there is no pagination. In a larger PR, test files beyond that point are ignored.
- An "added" case is any `+def test…` line in the PR's patch for that file, matched to slow cases by function name (parametrization stripped). An existing test that was moved or re-indented within the diff therefore also counts as added.
- Durations are the maximum across retries and slices, not the sum.
- Changing a test file without adding test functions counts toward the 60 s total, but never toward the single-case rule.
- Only the downgrade of an existing Warning is new. The 25% threshold and the baseline choice are unchanged.

## Plugin?

No. Core CI scripts and workflow.
