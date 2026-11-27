# Fail-closed startup readiness and natural controller rollover

Patch coverage: 6165, 6166, 6167, 6168, 6175, 6176, 6198, 6199, 6200, 6201, 6202, 6203, 6204, 6248, 6294, 6295.

## Behavior

Headless serving reserves its listening sockets before application assembly, but returns unavailable responses until the full application and authentication stack are ready. Health reports starting rather than claiming application readiness. Integration harnesses distinguish explicit service warmup from authentication and unrelated application failures.

Session-owner discovery that observed a restart and then timed out returns the newest starting/draining verdict, allowing the existing client restart recovery to retry. A timeout without restart evidence stays a timeout. Natural update can replace a recognized legacy standalone controller with a checkout-backed loader; it does not rewrite arbitrary entrypoints.

## How it works

The bootstrap responder runs independently of app assembly and hands the same sockets to ASGI after lifespan readiness. Header/connection deadlines prevent slow clients holding handoff; Windows uses exclusive binding. Resolved address families are retained on one selected port, IPv6 is v6-only, and unsupported/unavailable families may be skipped without swallowing permission or port conflicts. Diagnostics remain on stderr.

Gateway delivery and exit hooks resolve lazily rather than importing the agent stack to publish hooks. Signal handling touches only a fully initialized gateway. Rollover tries to reap superseded idle backends after the drain grace, preserves busy turns, and retains failed-stop age for retry. The same change retains bounded incremental native board counts keyed by file identity, evicting failed or replaced observations.

## Code

Affected implementation and integration paths in the source series:

- `hermes_cli/always_on_deployment.py`
- `hermes_cli/always_on_rollover.py`
- `hermes_cli/boot_warmup_gate.py`
- `hermes_cli/dashboard_server_start.py`
- `hermes_cli/fact_board_native.py`
- `hermes_cli/free_tier_bootstrap.py`
- `hermes_cli/main.py`
- `hermes_cli/serve_bootstrap_listener.py`
- `hermes_cli/serve_exit_flush.py`
- `hermes_cli/session_owner_discovery.py`
- `hermes_cli/sessions_early_boot.py`
- `hermes_cli/web_gateway_lazy.py`
- `hermes_cli/web_routers/chat_ws.py`
- `hermes_cli/web_server.py`
- `hermes_cli/web_server_app.py`
- `hermes_cli/web_server_lifecycle.py`
- `scripts/bench_serve_startup.py`
- `tui_gateway/methods_groups.py`
- `tui_gateway/startup_resume.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/e2e/core/test_service_readiness.py`
- `tests/test_docker_poll_stalled_exec.py`
- `tests/hermes_cli/test_serve_bootstrap_listener.py`
- `tests/hermes_cli/test_serve_bootstrap_addresses.py`
- `tests/hermes_cli/test_session_owner_discovery_starting.py`
- `tests/hermes_cli/test_always_on_deployment.py`
- `tests/hermes_cli/test_always_on_roller_drain.py`
- `tests/plugins/test_board_native_counts_incremental.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

A reachable socket is not a ready service. Harness retries recognize the exact warmup response, not every 503. Restart evidence can extend recovery for a genuinely hung owner; it is not proof a write was admitted. Controller symlinks, including dangling links, and unrecognized files are refused before restart. No rollout or startup benchmark was performed here.

## Why core, not a plugin?

Socket reservation, ASGI lifecycle, process exit, owner discovery and update-controller custody must be coordinated before plugin loading.
