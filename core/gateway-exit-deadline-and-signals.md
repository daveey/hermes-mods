# Gateway exit deadline, launchd signal forwarding and cron update admission

Patches:

- `patches/1092-fix-gateway-one-exit-deadline-tied-to-the-supervisor.patch`
- `patches/1106-fix-gateway-launchd-wrapper-forwards-stop-signals-an.patch`
- `patches/1107-daveey-cron-scoped-runs-drop-the-update-reservation-.patch`

Shutdown correctness for the gateway under systemd and launchd.

## Behavior

- 1092: one exit deadline (stop time + TimeoutStopSec / launchd ExitTimeOut / `HERMES_GATEWAY_EXIT_BUDGET_S`, minus min(5 s, budget/4)) covers the whole exit; on fire it dumps stacks, writes a `gateway.exit_deadline` record and `os._exit`s. Tail waits are clamped to the time left; routing saves use 2 s state.db patience while exiting.
- 1106: the launchd wrapper catches SIGTERM/SIGHUP/SIGUSR1/SIGUSR2, forwards them to its child and exits with the child's status; repeat stop signals in one shutdown count once; a start refused under `--external-supervisor` carries `external_supervisor=true`.
- 1107: scope-isolated cron runs drop the core-update admission reservation; a restart re-checks a withdrawn update intent after the wait.

## How it works

`gateway/exit_deadline.py` is armed by the stop-signal handler; signal helpers move to `gateway/run_signal_handlers.py`.

## Code

`gateway/exit_deadline.py`, `gateway/run.py`, `gateway/run_shutdown.py`, `gateway/run_shutdown_restart_wait.py`, `gateway/run_signal_handlers.py`, `hermes_cli/gateway_launchd.py`, `hermes_cli/gateway_exit_diag.py`, `cron/scheduler.py`.

## Tests

`tests/gateway/test_exit_deadline.py`, `tests/gateway/test_shutdown_forensics.py` (1092); `tests/gateway/test_repeat_stop_signals.py` and the launchd wrapper signals test (1106); `tests/cron/test_update_admission_hold.py` and the restart-cancel-on-update-giveup test (1107). The launchd path is exercised by wrapper tests only, not a live launchd job.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.
