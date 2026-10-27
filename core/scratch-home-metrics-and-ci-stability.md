# Scratch-home isolation, shared-metrics offer, and CI stability fixes

Patches **0479** (`f6a16700fd`), **0519** (`1fe662a3a4`), and the CI/test and small fixes **0465**, **0489**, **0491**,
**0492**, **0494**, **0495**, **0499**, **0524** (`bbb6290077`, `47721dc645`, `6b6a16c01a`, `fc979f0c25`, `c65a9c98b5`,
`4f7a8404c5`, `b032e92976`, `aa86510788`).

## Behavior

- 0479: a scratch Hermes home nested under the native root is its own root: profile enumeration never reads the
  native profiles, `profile=all` sidebar listing targets only the scratch home, a probe lock override ignores the
  real host record, and a symlinked native root still counts as native.
- 0519: `consent_state()` always reports `decided=True, reask=False`, with enabled/send from the profile's own config,
  so the composer strip, dashboard offer, pre-chat offer and end-of-setup offer never show the shared-metrics offer;
  opting in stays explicit (`hermes setup telemetry`).
- 0492: the Copilot ACP client drains the final response before reporting exit.
- 0465/0489/0491/0494/0495/0499/0524: portability and CI-flake fixes (Windows lint footguns in
  `kanban_provider_walls`/`core_update_drain`; canonical room reuse decoupled from lease timing; scheduler-starvation
  tolerance in delegation cancellation tests; connector RPC pipe delivery awaited; compression lock attempts
  synchronized; longer timeouts for CI-load-sensitive Desktop plugin/hub poller tests; ruff PIE807 and missing
  `getApiRequestConnection` mocks).

## How it works

Test-only or narrowly scoped edits; 0479 changes `hermes_constants.py` root resolution; 0519 changes
`hermes_cli/observability/shared_metrics_consent.py`; 0492 changes the Copilot ACP client.

## Code

`hermes_constants.py`, `hermes_cli/observability/shared_metrics_consent.py`, `agent/core_update_drain.py`,
`hermes_cli/kanban_provider_walls.py`, and the tests named below.

## Tests

`tests/test_scratch_home_isolation.py`; `tests/hermes_cli/test_setup_telemetry.py`,
`tests/tui_gateway/test_shared_metrics_consent_rpc.py` (one test per entry path: Desktop RPC status, dashboard read,
pre-chat offer, end-of-setup offer); the CI fixes are their own tests. 0465 and 0492 have no dedicated new test.

## Applying

`git am -3` in number order.

## Gotchas

0519 is a shipped-default decision (offer never shown); revert it if the offer should return.

## Plugin?

No: core constants, consent and test code.
