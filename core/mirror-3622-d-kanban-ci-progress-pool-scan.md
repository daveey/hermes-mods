# Kanban CI progress: pool scan when runner lists are unreadable

Patches: **3614–3615**.

## Behavior
- When both runner lists are unreadable, the CI-progress check scans the jobs of every in-progress run (one list call, up to 40 runs) plus up to 4 recently completed runs, looking for a job that holds a runner with the pool's labels. Previously only the first 4 runs were read, so a busy pool could be reported `not_starting`.
- A long-running in-progress job on the labels counts as busy. A readable runner list with only an offline runner still ignores recent jobs.
- One scan's answer is shared by cards waiting on the same pool for 60 s. Unreadable run lists or job reads are not remembered; one failed job read does not hide a match in another run. A scan cut off by the sweep budget is deferred and not remembered.
- Job lists are read 6 at a time on worker threads; each read runs with the caller's bound Hermes home, and the scan cache is keyed by home as well.

## How it works
- 3614 rewrites the fallback in `_recent_job_on`: reads all in-progress runs (`IN_PROGRESS_RUNS_MAX=40`), skips the queued run itself, reads job lists in batches of `POOL_SCAN_READERS=6`, checks the deadline before every list read and batch, and caches results for `POOL_SCAN_TTL_SECONDS` per (repo, labels).
- 3615 runs each threaded read inside `contextvars.copy_context()` so the gh command prefix comes from the bound home, and adds the home to the cache key.

## Code
- `hermes_cli/kanban_ci_progress.py`
- `tests/hermes_cli/test_kanban_ci_progress.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_ci_progress.py`: `test_unreadable_runners_find_the_pools_running_job_past_the_first_runs`, `test_unreadable_runners_with_a_long_running_job_on_the_labels_is_busy`, `test_readable_runner_list_with_only_an_offline_runner_ignores_recent_jobs`, `test_one_repo_scan_answers_every_card_on_the_same_pool_for_a_minute`, `test_repo_scan_cut_by_the_budget_is_deferred_and_not_remembered`, `test_unreadable_run_list_is_not_remembered`, `test_job_lists_of_a_scan_are_read_concurrently`, `test_one_unreadable_job_list_in_a_scan`, `test_pool_scan_reads_use_the_bound_home_on_its_reader_threads`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The deeper scan costs one broker vend plus one gh call per run (about 20+ calls for a busy repo), bounded by the sweep budget.
