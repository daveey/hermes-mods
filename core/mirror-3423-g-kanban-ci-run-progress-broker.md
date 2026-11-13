# CI-run progress check reads GitHub through the credential broker prefix

Patches: **3313, 3344**.

## Behavior
- The CI-run progress check runs `gh api` under the configured `kanban.pr_acceptance.gh_command_prefix`, so it works on a host where `gh` has no login. Before, every read failed open as unknown.
- With no usable credential it warns once an hour, per home; a missing `gh` is worded as "gh not installed", not a credential fix. An unreadable broker config fails open without leaking its text.
- The sweep binds the dispatching home even when run on a pool thread.

## How it works
- 3313 adds `kanban_gh_argv.dispatcher_gh_argv()` and uses it in the CI progress and PR-state paths.
- 3344 resolves the home on the dispatcher thread, binds it in `_sweep_db`, makes the warning timestamp per home, and updates the user docs.

## Code
- `hermes_cli/kanban_ci_progress.py`
- `hermes_cli/kanban_db_dispatch_pr_state.py`
- `hermes_cli/kanban_gh_argv.py`
- `website/docs/user-guide/features/kanban.md`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_ci_progress.py`: `test_run_read_goes_through_the_configured_broker_prefix`, `test_no_credential_warns_once_an_hour`, `test_unreadable_broker_config_fails_open_without_its_text`, `test_other_github_errors_are_not_credential_errors`, `test_refused_token_and_missing_gh_are_credential_errors`, `test_refused_token_is_a_credential_error`, `test_missing_gh_warns_gh_not_installed_not_a_credential_fix`, `test_sweep_on_a_pool_thread_uses_the_dispatching_homes_prefix`, `test_no_credential_warning_is_per_home`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
