# Launchd label propagation and missing-budget fallback

Patch **2063**. Companion to [gateway exit deadlines](gateway-exit-deadline-and-signals.md).

## Behavior

Generated gateway plists explicitly export `HERMES_LAUNCHD_LABEL`, allowing descendants below signed wrappers and launchers to identify their launchd job even when `XPC_SERVICE_NAME` is `0`.

If a Darwin externally supervised gateway cannot resolve its label or read its launchd budget, it assumes the 60-second GUI-domain clamp and emits a warning plus `gateway.launchd_budget_missing` exit diagnostics. Existing drain-reserve logic caps a longer drain to 50 seconds. Foreground Darwin and Linux gateways do not acquire this assumed budget.

## How it works

The plist's `EnvironmentVariables` uses the escaped actual job label. `_load_launchd_exit_timeout` passes a platform value to label/budget probes and delegates a missing result to `_assumed_launchd_exit_timeout`. That helper checks the external-supervisor environment flag and records `no_label` or `probe_failed`; diagnostic-write failure cannot prevent boot. The exit-diag tag is classified as warning.

## Code

- `hermes_cli/gateway_launchd.py`: generated plist environment.
- `gateway/run_config_loaders.py`: live budget lookup and fallback.
- `hermes_cli/gateway_exit_diag.py`: warning classification.

## Tests

**Not rerun this pass.** `tests/hermes_cli/test_launchd_label_reaches_gateway.py` checks default/custom generated labels; runs the signed-wrapper source, simulated downstream XPC reset, real `stderr_timestamp` and a leaf label-reader probe; tests missing-label Darwin external-supervisor budget/diagnostic behavior and foreground Darwin/Linux negative controls. The POSIX process-chain test does not boot a real launchd job. The `probe_failed` fallback branch is not separately tested in this patch.

## Applying

Apply 2063 using [the core replay](README.md). Existing installed plists need regeneration and controlled service reload to receive the new environment; changing source alone does not alter an already loaded job. No service was changed in this documentation pass.

## Gotchas

The assumed clamp is a fallback, not a successful live budget probe. The diagnostic preserves visibility of a lost label/probe. Existing stop-reserve behavior still determines the effective drain; this patch does not promise that arbitrary teardown finishes within it.

## Plugin eligibility

Not plugin-only: plist generation and shutdown budgeting run outside normal agent plugin lifecycle.
