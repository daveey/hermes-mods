# Cron scripts get the terminal's sane PATH, whichever process runs the tick

Patch: 0665.

## Behavior

A cron job's script (no-agent or pre-run) and the restart-safe cron worker now get the same PATH
normalisation the terminal tool applies: missing standard dirs, managed runtimes and `~/.local/bin`
are appended, and the caller's own PATH entries stay first. A script that calls a CLI installed in
`~/.local/bin` (pipx, uv tool, pip --user) works no matter which Hermes process won the cron tick
lock.

Each run document now has a `**Runner:** <gateway|serve|dashboard|cron-worker|other> pid N` line,
and agent.log names the runner on `Running job ... on <runner>` and `Cron script <name> running on
<runner>` lines, so a failure that only happens when one kind of process runs the job is visible.

## Why

While the gateway restarts during `hermes update`, another process takes the per-home cron tick
lock and runs the due jobs. A Desktop-over-SSH `hermes serve` backend inherits its PATH from a
non-login SSH shell, which has no `~/.local/bin`. The cron script env builder
(`_sanitize_subprocess_env`) left PATH as it was, so scripts calling `token-broker` failed with
`FileNotFoundError` only on the runs that backend picked up. They passed on the next gateway run,
so the failures looked flaky.

## How it works

- `cron/scheduler_script.py::with_sane_path(env)` runs `env["PATH"]` through
  `tools.environments.local._append_missing_sane_path_entries` (the terminal's helper). It does
  nothing on Windows.
- `_run_job_script` wraps `build_subprocess_env(strip_launch_profile=True)` with it.
- `_launch_external_cron_worker` wraps the worker env with it.
- `cron_runner_label()` gets the Hermes subcommand from `sys.argv[1:]` through
  `hermes_cli.update_cmd._hermes_holder_subcommand`, which matches tokens, not substrings. It
  returns `cron-worker` when the worker boot marker is set. `_job_doc_header` and `_run_doc_header`
  print it.

## Code

`cron/scheduler_script.py` (`with_sane_path`, `cron_runner_label`, `_run_job_script`) and
`cron/scheduler.py` (`_job_doc_header`, `_run_doc_header`, `run_job` log line,
`_launch_external_cron_worker`).

## Tests

`tests/cron/test_cron_script_minimal_path.py` (6 tests, all fail on the base commit):
- a `.py` script and a `.sh` script both find `~/.local/bin/fake-tool` with process PATH
  `/usr/bin:/bin`;
- a user PATH entry still wins over `~/.local/bin`;
- the external worker's `Popen` env PATH contains `~/.local/bin`;
- a no-agent run document names the runner with this pid;
- the runner label for `hermes serve`, `hermes gateway run`, `hermes dashboard`, the
  `hermes_cli/main.py --profile p gateway run` form, and the `python -c <bootstrap> serve` form.

The agent-path run document header (`_run_doc_header`) is covered only by the shared label
function, not by its own test.

## Applying

`git am core/patches/0665-*.patch`, then `scripts/run_tests.sh tests/cron/test_cron_script_minimal_path.py`.

## Gotchas

- Only PATH is normalised. Other env differences between processes, such as missing
  `SSH_AUTH_SOCK` or a different `HOME`, still depend on which process runs the tick.
- Other `build_subprocess_env` callers are not changed.

## Plugin?

No. The env is built inside the scheduler's spawn path, and there is no hook there.
