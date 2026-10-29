# fix(tests): scrub worker one-shot markers and reset session ContextVars per test [daveey]

Patch 0794, source `4cc6506eb1b970313bbf40a12f66c3ca90bed004`.

## Behavior

fix(tests): scrub worker one-shot markers and reset session ContextVars per test [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `_HERMES_BEHAVIORAL_VARS = frozenset({`
- `+"""Autouse reset of gateway.session_context state before every test.`
- `from tests._fixtures.live_system_guard import (`
- `+"""The hermetic test env drops agent-session markers a worker/agent shell exports, and resets`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> pytest run from a Kanban worker terminal inherited HERMES_SINGLE_QUERY_SESSION=1,
> so delegate_task charged the one-shot spawn budget against a MagicMock parent
> (test_delegate_apiserver_background, hosts only). The env filter now also clears
> it plus the worker/UI lineage markers HERMES_UI_SESSION_ID, HERMES_KANBAN_WORKER_TASK,
> HERMES_DELEGATED_CHILD_OWNER and HERMES_SUPERVISED_CHILD.
>
> A multi-file run shares one interpreter: test_kanban_provenance's
> clear_session_vars left every session ContextVar at "" (masking os.environ)
> and latched _session_context_engaged, so later files ignored their
> monkeypatched HERMES_SESSION_ID/HERMES_CRON_SESSION (4 tests in
> test_cronjob_tools, test_kanban_tools, test_completed_process_results, failing
> in a plain env too). A new autouse fixture resets session_context to
> 'never bound' before each test.
>
> Regression: tests/test_hermetic_env_agent_markers.py.

## Code

- `tests/_fixtures/env_filter.py`
- `tests/_fixtures/session_context_reset.py`
- `tests/conftest.py`
- `tests/test_hermetic_env_agent_markers.py`

## Tests

Source test files changed by this commit:

- `tests/_fixtures/env_filter.py`
- `tests/_fixtures/session_context_reset.py`
- `tests/conftest.py`
- `tests/test_hermetic_env_agent_markers.py`

New or changed test definitions present in the source diff:

- `test_worker_shell_markers_are_scrubbed_per_test`
- `test_single_query_marker_off_in_tests`
- `test_a_test_that_binds_and_clears_session_vars`
- `test_next_test_reads_env_through_session_vars`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
