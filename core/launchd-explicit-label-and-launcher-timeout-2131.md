# Explicit launchd label and launcher-provided exit timeout

Patches **2095, 2099**.

## Behavior

- 2095: `launchd_job_label()` accepts an explicit `HERMES_LAUNCHD_LABEL` whatever its prefix (hand-written plists with their own reverse-DNS labels), instead of only `ai.hermes*`. `XPC_SERVICE_NAME` still counts only with the `ai.hermes` prefix and wins when valid; `0`, blank and app-coalition names (`application.*`) are rejected in either variable. The stderr-timestamp wrapper re-exports the label with the same rule, so drain cap, restart route and supervisor declaration agree.
- 2099: a gateway sandboxed so it cannot exec `launchctl` can take `ExitTimeOut` from `HERMES_LAUNCHD_EXIT_TIMEOUT_S`, exported by an unsandboxed launcher that probed once. It is used (positive integer only; else ignored) only when the gateway's own probe fails or finds no `exit timeout` line and a label is known; a working live probe wins. The boot log notes "(from launcher)" and no `launchd_budget_missing` diagnostic is written in that case.

## How it works

`gateway/restart.py`: `launchd_job_label`, `LAUNCHD_EXIT_TIMEOUT_ENV`, `launcher_exit_timeout_s`, `read_launchd_exit_timeout_with_source` (returns value and source `launchctl`/`launcher`/None) over `_probe_launchd_exit_timeout`; `read_launchd_exit_timeout_s` delegates. `run_config_loaders` uses the source-aware reader and annotates logs. The launcher program that sets the env var lives outside this patch.

## Code

- `gateway/restart.py`, `gateway/run_config_loaders.py`, `hermes_cli/stderr_timestamp.py`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `tests/gateway/test_launchd_explicit_label.py`: explicit label counts whatever prefix; app-coalition XPC name rejected unless an explicit label follows; `ai.hermes` XPC wins; blank/app-coalition explicit label is no label; explicit label reads the job's live timeout; stderr wrapper forwards it. `tests/gateway/test_launchd_exit_timeout_from_launcher.py`: failed probe uses launcher value; garbage or missing value ignored; live probe wins; label required; boot uses launcher value with no missing-budget row; warning names the source; garbage still assumes the clamp. No real launchd job is booted.

## Applying

Apply ordinal(s) 2095, 2099 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. Companion docs: [launchd-label-stop-budget-2048.md](launchd-label-stop-budget-2048.md), [gateway-exit-deadline-and-signals.md](gateway-exit-deadline-and-signals.md), [custom-label-launchd-gateway.md](custom-label-launchd-gateway.md). The launcher side of 2099 is a separate component; without it the env var is never set and behaviour falls back to the assumed clamp. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

An explicit label is an operator assertion and is trusted; a wrong value yields a wrong probe target. Loaded jobs need a controlled reload to receive env changes.

## Could this be a plugin?

No. Shutdown budgeting runs before and outside the plugin lifecycle.
