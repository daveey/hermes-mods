# Gateway refusal detail and launchd test fence

Mirror entries: [2256](patches/2256-daveey-sync-fix-restart-gap-timeout-keeps-the-start-refusal-s-detail-quote-sys-executable-in-upstrea.patch), [2300](patches/2300-fix-gateway-test-processes-never-load-jobs-into-the-real-launchd-GUI-domain-daveey.patch), [2301](patches/2301-test-gateway-remove-opted-in-launchd-jobs-on-assertion-failures-daveey.patch).

## Behavior

- A restart-gap timeout preserves the original service_identity_unverified refusal and its diagnostic detail.
- Test processes do not bootstrap real launchd GUI jobs unless HERMES_TEST_ALLOW_LAUNCHD=1 explicitly opts in.
- Opted-in launchd probes clean up their jobs even after an early assertion failure.

## How it works

The restart observer receives the original GatewayDiscovery and returns it on timeout. The service-detail test shell-quotes sys.executable. launchd startup checks the shared test-context detector, including pytest ancestry when a child rebuilds its environment; the explicit opt-in allows dedicated launchd tests through. 2301 strengthens test cleanup.

## Code

- `hermes_cli/gateway_runtime.py`
- `hermes_cli/gateway_launchd_runtime.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/hermes_cli/test_gateway_runtime_restart_gap.py` — `test_an_owner_that_never_returns_keeps_the_start_refusal_and_its_detail`.
- `tests/hermes_cli/test_gateway_runtime_service_detail.py`
- `tests/hermes_cli/test_gateway_launchd_runtime.py` — `test_gateway_job_is_a_launchd_osascript_child_that_reads_as_unsupervised`, `test_a_test_process_never_touches_the_real_launchd_domain`, `test_the_opt_in_lets_a_test_reach_launchd`, `test_outside_a_test_the_job_is_bootstrapped` (additional cases in this file).

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

The restart wait observes a returning owner; it does not execute an unverified service definition. The launchd guard is test isolation, not production authorization: absence of the guard module preserves normal startup. No real launchd probe was run for this documentation pass.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
