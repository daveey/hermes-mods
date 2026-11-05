# persist_on_release jobs leave any spawning systemd user unit

Patch **2000**: source `78a282ac5de6a8fb093f458b75d45a1451e33a4e`, "fix(process): persisted jobs survive a restart of
any spawning user unit [daveey]". This extends [process-persist-on-release-survival.md](process-persist-on-release-survival.md)
(patch 1904). Before this patch, only a Kanban run scope got this treatment.

## Behavior

- **Any user unit, Linux.** A `persist_on_release` background job whose spawner runs inside *any* systemd user unit
  gets its own `systemd-run --user --scope`, named `hermes-worker-<proc>.scope`. A user unit here means a
  `.service` or `.scope` below `user@<uid>.service`, for example a Desktop backend service with
  `KillMode=control-group` that an update restarts. Stopping or restarting the spawner's unit no longer kills
  the job. `session.systemd_unit` is set, so killing the job still works.
  - **Kanban run scopes** keep the 1904 behaviour: `hermes-kanban.slice` and the run scope's memory cap.
  - **Other user units** put the job in `app.slice` with the spawner unit's own `memory.max`. If the spawner
    is uncapped, the job gets `MemoryMax=infinity`, not the 4 GiB worker default.
- **No user unit, unchanged.** A spawner that isn't in a user unit keeps the old spawn: a system service,
  the cgroup root, or a login `session-N.scope`. So does a host with no user bus or no `systemd-run`.
  The job runs as a plain child.
- **Log instead of pipe.** A persisted job writes to the process-output log (`<HERMES_HOME>/logs/process-output/<id>.log`)
  instead of a pipe, on POSIX. Moving the job into its own scope was not enough by itself: its first write
  after the spawner exited raised SIGPIPE.
  - A persisted `pty=true` request runs on the log without a PTY, because the backend's PTY master would
    SIGHUP the job when the backend exits. The job records why in `pty_fallback`.
  - Stdin errors say "persist_on_release jobs write to a log file so they outlive the backend, with no
    pseudo-terminal". They no longer claim an isolated worker.
- **Restart advice.** A killed job's receipt now adds "restart it with persist_on_release=true so it survives
  updates" to the restart advice. The terminal tool schema's `persist_on_release` description now says the job
  survives "backend restarts and Hermes updates (not host exit)". The prompt-surface fixtures are updated
  to match.

## How it works

- `tools/process_registry_persist_scope.py`:
  - `_own_cgroup` reads the `0::` line of `/proc/self/cgroup` (`PROC_SELF_CGROUP`), and the cgroup root
    is `CGROUP_ROOT`. Both are module constants, so tests can redirect them.
  - `user_unit_cgroup` finds a `user@*.service` path component that has a `.service` or `.scope` leaf
    below it.
  - `persisted_job_scope_argv` prefers the Kanban run scope, then any user unit. It reads that unit's
    `memory.max` and picks the slice and cap.
- `tools/process_registry._systemd_scope_argv` accepts a string `memory_max`, which allows `"infinity"`.
- `tools/process_registry_output_log.worker_output_log(id, persist=)` returns a log path for a persisted job,
  except on Windows (`os.name == "nt"`). `spawn_output_log(session, use_pty)` sets `pty_fallback` to
  `PERSIST_NO_PTY` for a persisted PTY request. `ProcessRegistry.spawn_local` uses it.
- `tools/process_registry_stdin._no_pty_reason` gives the reason text for persisted jobs.
- `tui_gateway/update_pause.PERSIST_RESTART_HINT` is appended in `killed_jobs_report`, which builds receipts
  for both the update pause and backend shutdown.
- `tools/terminal_tool.py` updates the schema text. `tests/fixtures/prompt_surface/*.json` are regenerated.

## Code

`tools/process_registry_persist_scope.py`, `tools/process_registry.py`, `tools/process_registry_output_log.py`,
`tools/process_registry_stdin.py`, `tools/terminal_tool.py`, `tui_gateway/update_pause.py`.

## Tests

I didn't run these tests for this document. They're listed from the source diff.

- `tests/tools/test_persist_job_own_scope.py`:
  - `test_persisted_job_outside_any_user_unit_is_unchanged`. This is the renamed
    `test_persisted_job_outside_kanban_run_scope_is_unchanged`.
  - `test_persisted_job_from_any_user_service_gets_own_app_slice_scope` (Linux). A fake cgroup for a Desktop
    backend `.service` gives `--slice=app.slice`, `MemoryMax=infinity` and the unit name.
  - `test_persisted_job_inherits_a_capped_spawner_units_memory_max` (Linux).
  - `test_persisted_job_outside_a_user_unit_is_unchanged[system.slice/hermes.service||user.slice/…/session-3.scope]` (Linux).
  - `test_real_persisted_job_survives_stop_of_its_spawning_user_service[sleep|writes-output-after-spawner-exit]`.
    This is a real integration test. It needs Linux with a user systemd bus and is skipped otherwise.
    It starts a transient `KillMode=control-group` service that spawns the job, stops the service, and
    checks that the job is alive, keeps printing, and runs in `/app.slice/<unit>`.
- `tests/tools/test_persist_on_release.py::test_persisted_spawn_writes_a_log_and_says_why_it_has_no_pty`.
- `tests/tui_gateway/test_shutdown_job_receipts.py::test_shutdown_receipt_for_killed_notify_job_says_how_to_survive_updates`.
  This covers the backend-shutdown receipt.

The commit message says RED before the change and GREEN after, for the unit, integration and receipt cases.
That's the implementer's result. I didn't reproduce it.

**Untested:**

- The update-pause receipt. It shares `killed_jobs_report` with the shutdown receipt, but no test
  checks the hint on the update-pause path.
- The next backend re-adopting the log after a real update.
- Windows, which keeps the pipe by design.
- macOS. No systemd there, so the path is unchanged.
- A real hub update cycle.

## Applying

Apply with the series (README § Applying), after 1904, whose `process_registry_persist_scope.py` this rewrites.
The prompt-surface fixtures must match the schema text. Regenerate them if the description conflicts.

## Gotchas

- **Unit cap.** The job inherits the spawner unit's cap. If the spawner's unit is capped below what the job
  needs, the job now runs under that cap in `app.slice`, where it previously shared the service's cgroup.
- **No PTY.** A persisted `pty=true` job never gets a PTY on POSIX. Interactive tools that need a TTY won't work as persisted jobs.
- **Login sessions.** A spawner in a login session scope (`session-N.scope`) isn't covered: the job stays in
  that scope and still dies with the session.

## Plugin boundary

There's no plugin equivalent. The spawn argv and the stdout wiring are decided inside `ProcessRegistry.spawn_local`.
