# Optional terminal process survival across backend updates

Patches **2169–2175**. Related: [process-persist-any-user-unit.md](process-persist-any-user-unit.md) and [process-readoption.md](process-readoption.md). The update-cancel result compatibility fix **2176** is documented in [update-pause-mid-tool-cancel-2215.md](update-pause-mid-tool-cancel-2215.md).

## Behavior

`terminal.update_survivable_processes` defaults to `false`. On Linux, with a usable systemd user scope and a spawner inside a recognized user unit:

- Local pipe-backed background jobs default to `persist_on_release=true`; explicit false wins. Implicit persistence does not replace a requested PTY.
- Local foreground commands with effective timeout **above 60 seconds** can run in their own scope. Update-pause, gateway-shutdown or backend-exit interrupts hand them to the process registry instead of killing them; the terminal result has `status: continues_in_background`, a process `session_id`, and a final `process continues as <session_id>` line.
- A later backend can recover checkpointed jobs and deliver completion. User Stop and ordinary timeout still kill a foreground command; a later survivable interrupt cannot override a pending Stop in either turn or tool-thread state.
- Without a usable scope, the option preserves ordinary behavior and emits one warning per process.

## How it works

`SurvivableForeground` wraps the local spawn using the existing persisted-job scope builder. Output is teed to a durable log with a sibling exit-status file. A foreground hold is checkpointed in `processes.json` but not exposed as a running background job until handoff. Handoff reuses that scope/log, including yield-to-background; normal completion releases the hold and files. Recovery uses the existing PID/start-time machinery.

2170–2173 refine the shutdown sweep: a persisted job survives gateway shutdown only when detached, or when it actually has a scope and this option remains enabled. The wrapper uses process-substitution tee with a bounded drain, so a self-daemonizing command holding stdout does not keep the foreground wait open. Logs strip only this session's checkpointed shell markers, even across read boundaries, and do not delay ordinary non-newline progress text.

2171 changes the daemon regression to a self-forking command rather than shell background syntax. 2174 waits for the actual command in a persisted job's cgroup before stopping its service/scope in integration tests. 2175 makes interrupt-reason lookup lazy for import compatibility and delegates executable discovery to the managed systemd probe instead of bare `which`.

## Code

`tools/process_registry_survivable.py`, `tools/process_registry{,_checkpoint,_output_log}.py`, `tools/environments/{base,local}.py`, `tools/{terminal_tool,interrupt}.py`, `agent/interrupt_control.py`, `hermes_cli/config_defaults.py`; configuration documentation in `website/docs/user-guide/configuration.md`.

## Tests

**Upstream-supplied coverage; not executed in this mirror documentation pass.**

- `tests/tools/test_update_survivable_processes.py` covers default/explicit persistence, key-off/non-Linux/nonlocal/PTY/timeout eligibility, unusable or absent user units, interrupt classification, checkpoint holds and wrapper exit status.
- Its real-systemd cases cover owner-service stop, recovery with one completion, killing a recovered job, key-off death, foreground shutdown survival, user-Stop kill and prompt return from a self-daemonizing tool. They require Linux and a usable user systemd environment; their presence is not evidence they ran here.
- The same file covers pending Stop against survivable reasons, key-gated scoped shutdown exemption, exact markers, marker splits across reads and immediate progress text.
- `tests/tools/test_persist_job_own_scope.py` receives readiness synchronization for `test_real_persisted_job_survives_stop_of_its_spawning_user_service` and `test_real_persisted_job_lands_outside_run_scope` (2174).

This documents terminal/core registry paths, not an executed `execute_code` entry-path test or a live update rollout.

## Applying

Use the full ordered replay in [README.md](README.md), including its replay plan and integration bridges; do not apply these mail patches independently. After replay, enable the optional configuration only in an environment supporting the required user scopes.

## Gotchas

Survival is backend-restart survival, not host-reboot survival. A plain unscoped child cannot escape a unit's control-group kill merely by setting persistence. Explicit persisted PTY requests retain their existing log-backed fallback; the new implicit default deliberately leaves PTY jobs alone. Treat a handoff as continuing work, not a command to rerun. Keep the option enabled through shutdown if relying on the new scoped-job exemption.

## Plugin?

No. Process spawning, registry checkpoints, output logs, foreground waits and interrupt precedence must change together in core.
