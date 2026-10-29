# Provider fallback failures and token-unit model routing

Patches **0486**, **0487** (sources `1164b8813f`, `582fab6bfb`) and **0511**, **0527** (`d83286a1bd`, `d56b621070`).

## Behavior

- A fallback ladder that recovers still warns about the entry that failed; a failed fallback is logged and surfaced
  in cron and in the gateway reply, and the CLI reports a secondary startup failure (0486).
- Quota holds need the entire chain to be quota-exhausted: an all-quota chain keeps its classification and the
  earliest window (an invalid hint publishes no window), an all-relogin chain keeps terminal-auth classification,
  mixed chains inherit neither, an empty chain has none (0486). TUI "not configured" envelopes are produced only for
  a homogeneous chain.
- Fallback diagnostics are kept within health caps (refactor, 0487).
- Model-routing context caps compare a token estimate, not UTF-8 bytes (0511); auxiliary class routing does the
  same, and `title_generation` is served without attested JSON while other tasks still require it (0527).

## How it works

`hermes_cli/fallback_errors.py` and `cron/quota_hold.py` classify chains; `agent/agent_init.py`,
`agent/agent_init_routes.py`, `gateway/run_turn_{runner,resolution_errors}.py` and `tui_gateway/server.py` surface
them; `agent/model_routing_request.py` and `agent/auxiliary_class_routing.py` use the compression/preflight rough
token estimator.

## Code

Files above, plus `cron/scheduler.py`, `hermes_cli/runtime_provider.py`, `hermes_cli/cli_agent_setup_mixin.py`.

## Tests

`tests/agent/test_init_fallback_refusal_warning.py`, `tests/cron/test_fallback_failure_details.py`,
`tests/gateway/test_fallback_failure_details.py`, `tests/hermes_cli/test_fallback_error_classification.py`,
`test_resolution_failure_details.py`, `tests/tui_gateway/test_fallback_failure_details.py` (cron, gateway, TUI and CLI
entry paths each have a test); `tests/agent/test_model_routing_request_context.py`,
`test_auxiliary_class_requirements.py` (bytes over cap but tokens under cap passes; genuinely oversized still fails).

## Applying

`git am -3` in number order. 0487 depends on 0486.

## Gotchas

The estimator is rough: caps are not a hard tokenizer guarantee, so oversize requests still fail closed.

## Plugin?

No: provider resolution and routing are core.
