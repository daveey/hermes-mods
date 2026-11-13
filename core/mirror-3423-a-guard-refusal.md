# Kanban: slow plugin guard no longer costs a worker its park/report

Patches: **2980, 2981, 2982**.

## Behavior
- A timed-out fail-closed pre-tool-call guard refuses with a message naming the plugin and when to retry; lifecycle tools (park, comment, heartbeat) still run during suppression while `kanban_complete` stays fail-closed.
- A run whose terminal kanban calls were refused is requeued uncounted without a failure notice instead of booked as a protocol violation; a run with no kanban calls, or an old refusal outside the tail, stays a violation; the refusal streak is bounded.
- Classification requires the tool result itself to be the refusal (not a card body quoting it), only for terminal tools, and tolerates an appended budget notice.

## How it works
2980 adds `kanban_guard_refusal` and dispatch handling plus plugin dispatch changes; 2981 parses JSON and restricts to terminal tools; 2982 marks the read-only state read for the lockguard and strips budget notices.


## Code
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_guard_refusal.py`
- `hermes_cli/plugins_dispatch.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_guard_refusal.py`: `test_non_refusal_kanban_results_stay_a_protocol_violation`, `test_old_refusal_outside_the_tail_stays_a_protocol_violation`, `test_refusal_streak_is_bounded`, `test_refusal_with_appended_budget_notice_still_counts`, `test_refused_kanban_calls_requeue_uncounted_without_notice`, `test_refused_kanban_complete_counts_as_host_refusal`, `test_run_without_kanban_calls_stays_a_protocol_violation`.
- `tests/hermes_cli/test_plugins_lifecycle_exempt.py`: `test_kanban_complete_stays_fail_closed`, `test_lifecycle_call_that_times_out_itself_runs`, `test_lifecycle_tool_reaches_its_handler_e2e`, `test_lifecycle_tools_run_during_suppression`, `test_timeout_refusal_names_plugin_and_retry`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
Counting depends on the tail of the worker's recorded tool results.
