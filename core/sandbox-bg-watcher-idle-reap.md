# Sandbox background watchers survive idle environment cleanup

Patches: 1262

- `patches/1262-fix-process-keep-a-sandbox-job-s-env-through-the-idl.patch`

## Behavior

An idle profile-keyed sandbox environment stays cached while its registered background process is running. If a poller's environment handle fails but its recorded sandbox can be reattached, polling resumes and delivers the real output and exit rather than reporting the process lost. If no environment can be reattached, the session still terminates as lost.

## How it works

`_cleanup_inactive_envs` supplements the raw-owner lookup with `_any_running` on `ProcessSession.task_id` for every environment key, not just workspace digests. An active match refreshes that key's last-activity time.

The non-local poller moves into `process_registry_env_log.py`. On an execution exception it attempts a fresh environment handle from `session.sandbox_handle`, rejects a sandbox whose reported start is more than five seconds after the process start, and requires the wrapper PID file to exist. It updates `session.env_ref` and continues incremental byte-offset reads. At most three consecutive reattachments are attempted without a successful poll; success resets that counter. Existing kill settlement remains authoritative.

## Code

`tools/terminal_tool_lifecycle.py`, `tools/process_registry.py`, and `tools/process_registry_env_log.py`.

## Tests

`tests/tools/test_sandbox_bg_watcher_survives_idle_reap.py` includes:
- `test_reaper_keeps_a_profile_container_env_while_its_background_job_runs`: a profile-key environment remains while active, then becomes eligible for teardown after exit.
- `test_poller_whose_env_dies_reattaches_and_delivers_the_real_exit`: a shell-backed sandbox stand-in loses its handle, reattaches, and reports actual output and exit.
- `test_poller_reports_lost_only_when_the_sandbox_cannot_be_reattached`: unavailable reattachment produces the lost outcome.

These tests were not re-run for this documentation update. They are not live Docker, SSH, Modal or other provider end-to-end runs; those provider entry paths are untested here. The restarted-sandbox timestamp refusal and three-consecutive-reattach exhaustion are implementation guards without dedicated new cases in this patch.

## Applying

Apply the complete numbered series at the documented merge-base with `git am -3 --empty=keep`; see `README.md`. Preserve the earlier sandbox-handle recording and reattach support.

## Gotchas

- Keeping an environment handle is not evidence that its remote job survived; successful reattachment still requires the recorded sandbox and PID file.
- This changes non-local environment polling, not the local PTY reader.
- Reattachment does not recreate a missing sandbox or restart the command.
