# Scratch-home isolation, shared-metrics offer, and CI stability fixes

Patches **0485** (`f6a16700fd`), **0526** (`1fe662a3a4`), and the CI/test and small fixes **0471**, **0496**, **0498**,
**0499**, **0501**, **0502**, **0506**, **0531** (`bbb6290077`, `47721dc645`, `6b6a16c01a`, `fc979f0c25`, `c65a9c98b5`,
`4f7a8404c5`, `b032e92976`, `aa86510788`).

## Behavior

- 0485: a scratch Hermes home nested under the native root is its own root: profile enumeration never reads the
  native profiles, `profile=all` sidebar listing targets only the scratch home, a probe lock override ignores the
  real host record, and a symlinked native root still counts as native.
- 0526: `consent_state()` always reports `decided=True, reask=False`, with enabled/send from the profile's own config,
  so the composer strip, dashboard offer, pre-chat offer and end-of-setup offer never show the shared-metrics offer;
  opting in stays explicit (`hermes setup telemetry`).
- 0499: the Copilot ACP client drains the final response before reporting exit.
- 0471/0496/0498/0501/0502/0506/0531: portability and CI-flake fixes (Windows lint footguns in
  `kanban_provider_walls`/`core_update_drain`; canonical room reuse decoupled from lease timing; scheduler-starvation
  tolerance in delegation cancellation tests; connector RPC pipe delivery awaited; compression lock attempts
  synchronized; longer timeouts for CI-load-sensitive Desktop plugin/hub poller tests; ruff PIE807 and missing
  `getApiRequestConnection` mocks).

## How it works

Test-only or narrowly scoped edits; 0485 changes `hermes_constants.py` root resolution; 0526 changes
`hermes_cli/observability/shared_metrics_consent.py`; 0499 changes the Copilot ACP client.

## Code

`hermes_constants.py`, `hermes_cli/observability/shared_metrics_consent.py`, `agent/core_update_drain.py`,
`hermes_cli/kanban_provider_walls.py`, and the tests named below.

## Tests

`tests/test_scratch_home_isolation.py`; `tests/hermes_cli/test_setup_telemetry.py`,
`tests/tui_gateway/test_shared_metrics_consent_rpc.py` (one test per entry path: Desktop RPC status, dashboard read,
pre-chat offer, end-of-setup offer); the CI fixes are their own tests. 0471 and 0499 have no dedicated new test.

## Applying

`git am -3` in number order.

## Gotchas

0526 is a shipped-default decision (offer never shown); revert it if the offer should return.

## Plugin?

No: core constants, consent and test code.
