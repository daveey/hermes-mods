# session.info keeps a queued model switch's provider

Patch **0556** (source `3458007706`).

## Behavior

`session.info` reports the provider of a queued model switch while it is pending. A merge of credential-failure fields into
every `session.info` with a live agent had overwritten it with the live agent's provider
(`test_session_info_reports_pending_model_switch`: `'openai' == 'anthropic'`). The failure fields are now merged except
`provider`; the two only differ while a switch is queued, and then the next turn runs on the queued provider.

The same commit stops goal and session-control tests from faking `Path.home()`: their `hermes_home` fixtures set both
`Path.home() = tmp_path` and `HERMES_HOME = tmp_path/.hermes`, which looks like the real user store to the auth-store seat belt
("Refusing to touch real user auth store"). `tests/conftest.py` already isolates `HERMES_HOME`, so the patch was redundant and is removed.
It also makes foreground-command exit cleanup log a failed kill as a warning.

## How it works

`tui_gateway/server.py::_session_info` merges `credential_failure_fields(...)` minus the `provider` key.
`tools/environments/base.py` logs the exit-cleanup kill result.

## Code

`tui_gateway/server.py`, `tools/environments/base.py`, `tests/tui_gateway/test_goal_command.py`, `tests/tui_gateway/test_session_control.py`.

## Tests

`tests/tui_gateway/test_tui_gateway_server.py::test_session_info_reports_pending_model_switch` (existing, red before the fix), and
`tests/tools/test_local_interrupt_cleanup.py`: `test_exit_cleanup_kills_foreground_command_still_running` and
`test_failed_exit_cleanup_kill_is_logged_as_a_warning`. No new test covers the merge itself; the existing pending-switch test does.

## Applying

`git am -3`; needs the credential-failure fields patch.

## Gotchas

A field shared between `session.info`'s own identity and a merged dict must be excluded explicitly: a new merge into `session.info` needs a test that checks the merged result, not the fields alone.
