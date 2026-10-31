# Nightly E2E dispatch and actionable reports

Patches: 1354 (`5c77befe865`), 1355 (`a87b03cf637`),
1356 (`4b080e37087`), 1357 (`9722d7272fa`).

## Behavior

- A dispatch-only workflow runs the same reusable E2E job as the upstream-sync
  gate, with Python lanes and E2E-upgrade disabled; Python defaults on for other callers.
- Dispatch records the resolved SHA and newly observed run. Reporting files one
  card per sorted failing-test set; repeats comment on an open card, different
  failures get separate cards, and archived cards are replaced with fresh keys.
- Green runs comment on open tracked cards without closing them automatically.
- Missing runs after 30 minutes, unfinished runs after four hours, and cancelled
  runs produce a shared did-not-run finding. Persistent lookup/log errors past
  four hours produce that finding too, rather than silently abandoning the run.
- If the board also fails, pending work survives and `report` exits 1.
- Concurrent report/dispatch processes serialize state changes without losing a dispatch.

## How it works

`scripts/ci/nightly_e2e.py` exposes `dispatch` and `report`, with `--ref`,
`--repo`, `--workflow-ref`, `--state`, and `--hermes` options. State defaults to
`~/.hermes/state/nightly-e2e.json`; an exclusive file lock covers load through
atomic save. Reads use UTF-8 with optional BOM; BOM-specific behavior is untested.
The reporter parses pytest `FAILED` and `ERROR` summary IDs, ignores live copies,
and hashes the sorted IDs for deduplication. Failures outside pytest still file a card.
GitHub calls use `GH_TOKEN` or `GITHUB_TOKEN`; board operations use the CLI adapter.

## Code

- `.github/workflows/nightly-e2e.yml`, `.github/workflows/tests.yml`.
- `scripts/ci/nightly_e2e.py`, `tests/ci/test_nightly_e2e.py`.

## Tests

Names below are in `tests/ci/test_nightly_e2e.py`; inspected, not executed here:
- `test_nightly_workflow_is_dispatch_only_and_calls_the_gates_e2e_job`,
  `test_python_input_defaults_on_and_gates_only_the_python_lanes`.
- `test_dispatch_records_the_new_run`, `test_second_dispatch_does_not_adopt_the_first_run`.
- `test_failing_tests_are_sorted_unique_and_skip_live_copies`,
  `test_failing_tests_include_pytest_error_summary_ids`, `test_failure_key_depends_only_on_the_set`.
- `test_failed_run_files_one_card_with_run_and_tests`,
  `test_second_failure_with_same_tests_updates_the_card`,
  `test_different_failing_tests_get_their_own_card`, `test_archived_card_is_replaced_not_commented`.
- `test_green_run_files_nothing_and_clears_pending`, `test_green_comments_on_open_failure_card_only`.
- `test_run_still_going_within_deadline_stays_pending`,
  `test_run_not_finished_in_four_hours_files_did_not_run_card_once`,
  `test_dispatch_that_never_creates_a_run_is_reported`, `test_cancelled_run_is_a_did_not_run_card`.
- `test_failure_outside_pytest_still_files_a_card`,
  `test_persistent_github_error_files_did_not_run_card_past_deadline`,
  `test_board_failure_past_deadline_keeps_run_pending_and_says_so`,
  `test_main_report_exits_1_and_keeps_pending_when_board_fails`.
- `test_concurrent_report_and_dispatch_processes_do_not_lose_the_dispatch`, `test_state_round_trip`.

## Applying

Apply 1354–1357 in order. Provision external dispatch/report scheduling on an
always-on Linux host and an Actions-write credential; the patches do not install cron jobs.
Pass `--repo` for the target fork instead of relying on the source default.

## Gotchas

There is no GitHub `schedule` trigger. Hosted execution, credential injection,
real GitHub redirects, board writes, and scheduler delivery are untested here;
most tests use fake GitHub/board objects, while the locking test uses real processes.
No plugin is installed by this series; it uses the existing Kanban CLI infrastructure.
