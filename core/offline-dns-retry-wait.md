# Offline DNS retry wait

Patches **1425–1427, 1469**, frozen source `3241b23316a66eb4d41c26078cd92411f0f295bc`.

## Behavior

A model connection error whose cause chain contains a local network/DNS failure can enter an offline wait before spending provider retries. Both the provider hostname and a control hostname must fail resolution. `agent.offline_wait_seconds` defaults to **900**; **0 disables** the wait. Repeated outages within one model request share a cumulative budget rather than resetting it on each failure.

The wait publishes an offline status, feeds the watchdog, and remains interruptible. Interrupt handling preserves a queued redirect or returns an abort. Cron retains the original provider error type when a model turn fails.

## How it works

The retry classifier walks the error cause chain. Provider/control DNS observations distinguish a presumed local outage from an unresolved provider name while the control host resolves. The latter logs a diagnostic and falls through to ordinary retries. Provider DNS success is permission to retry, not proof of provider reachability.

The budget measures monotonic elapsed time (awake time on macOS), including initial DNS work, and caps each poll to the remaining allowance. Native DNS lookups are isolated behind interruptible bounded observation; at most eight native probes may remain active. Exhaustion falls back to ordinary retry accounting. Configuration parsing rejects invalid/nonfinite values. Retry initialization and defaults live in separate small modules; cron wraps failed turns without discarding provider classification metadata.

## Code

- `agent/retry_utils.py`: cause-chain classification, hostname extraction, bounded DNS probes and network wait.
- `agent/turn_offline_wait.py`: per-request remaining allowance, status/watchdog and interrupt/redirect integration.
- `agent/turn_api_error.py`, `agent/turn_retry_state.py`: error-handler entry and request-local consumed budget.
- `agent/agent_init_retries.py`, `hermes_cli/config_defaults_retries.py`: configuration and defaults.
- `cron/scheduler.py`: `AgentTurnFailed` and error-type preservation.
- `website/docs/user-guide/configuration.md`, `cli-config.yaml.example`: user-facing configuration and limitations.

## Tests

These are source tests carried by the patches, **not rerun in this mirror pass**:

- `tests/agent/test_offline_wait.py`: real turn retry path survives simulated DNS outage without spending retries; disabled mode retains ordinary failure; DNS recovery, monotonic/wall-clock separation, awake-budget exhaustion, initial DNS accounting, interrupt-before-DNS, control-host diagnostic and cause-chain classification.
- `tests/agent/test_offline_wait_boundaries.py`: hung native DNS is bounded and observation is interruptible; timeout does not await native completion; repeated outages share one request budget and feed the watchdog; non-offline verdicts fall through; redirects/aborts and invalid configuration.
- `tests/cron/test_cron_offline_wait.py`: `test_cron_run_that_goes_offline_mid_run_still_finishes_ok` and `test_offline_failure_keeps_the_provider_error_type` exercise the cron path.

Patch 1469 changes only `tests/cron/test_cron_offline_wait.py`: the child imports real `hermes_bootstrap` first, then restores parent test-interpreter site/dist-packages from `OFFLINE_TEST_DEPENDENCY_SITES`. This keeps pytest available after a borrowed-home PM re-exec; production bootstrap, fences and assertions are unchanged.

Implementation-owner evidence (not rerun by this mirror): managed fresh-home RED had two `ModuleNotFoundError: pytest` failures; GREEN had 29 passing tests (two real fake-server scheduler/DNS cases plus 27 agent tests), also 29 in the developer worktree. Recovery asserts `remaining=0`, `marker=true`, job `ok` and two served requests; disabled wait retains `APIConnectionError`. PR #473 merged as `3241b23316a6`, with the required aggregate successful on exact tested head `d9540a16e1d772442e28df8103f5f5afb812e6b1`.

The public mirror's independently executed inventory, replay and normalized-tree checks are recorded in [mirror-1469-verification.md](mirror-1469-verification.md). Physical-event acceptance remains **NO_PROOF**: a real Mac sleep/wake run is not proved by simulated-clock or harness tests and is not claimed here.

## Applying

Use the full bridge-aware series recipe in [README.md](README.md#applying), with the 1469 policy and replay plan. These patches depend on prior retry/configuration changes and are not advertised as independently applicable.

## Gotchas

- DNS recovery does not establish TCP/TLS/API reachability. Cached DNS, proxies and literal-IP endpoints can remain on ordinary retries.
- A resolving control host with an unresolved provider is treated as provider-specific failure, not a local outage.
- Native resolver calls cannot be forcibly killed; the concurrency bound limits retained probe threads.
- This is a cumulative **per-request** budget, not an unlimited wait or a whole-job deadline.
- Actual laptop sleep/wake behavior remains a separate live acceptance exercise.

## Why not a plugin?

The feature owns core retry accounting, request state, initialization and cron failure metadata. A notification-only plugin cannot safely preserve these invariants.
