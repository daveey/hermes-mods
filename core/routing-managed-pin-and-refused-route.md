# Native managed endpoints and switching away from refused routes

Patches: 1353 (`d464e6a60e1`), 1360 (`a85307bb6ed`).

## Behavior

- Managed concrete pins accept an explicit URL belonging to their own provider,
  including an adopted chat's frozen native endpoint, for Anthropic and Codex.
- Foreign/unknown endpoints, unspecified or `auto`/`custom` providers with a URL,
  and explicit API keys remain refused before managed allocation.
- A local chat's policy refusal is a non-retryable gateway-layer `route_refused`,
  not a rejected sign-in; plain credential failures retain `auth_permanent`.
- Model-pick preparation can leave a policy-refused current route and prepare
  a new route using the configured old provider/model with no current API key.

## How it works

`overrides_endpoint` normalizes the provider and requires `provider_owns_route`
to affirm URL ownership. Empty URLs are not overrides; `local` URLs remain
overrides too (that specific provider case is untested by the new tests).
The runtime resolver combines this predicate with the existing explicit-key guard.
`_resolution_failure_reply` distinguishes local `ModelRoutingError` failures.
`_current_route` catches `ModelRoutingError` alongside `AuthError`, so resolving
the old route cannot prevent preparing the replacement.

## Code

- `agent/model_routing_managed.py`, `hermes_cli/runtime_provider.py`.
- `agent/error_surface.py`, `gateway/run_turn_runner.py`.
- `gateway/session_mutation_model.py`.

## Tests

`tests/gateway/test_managed_pin_native_endpoint.py`:
- `test_resolver_serves_a_pin_on_its_own_endpoint`.
- `test_an_adopted_chat_route_runs_on_its_frozen_native_endpoint` (both fallback modes).
- `test_a_real_endpoint_override_is_still_refused`.
- `test_an_explicit_key_is_still_refused`.
- `test_a_routing_refusal_on_a_local_chat_is_not_a_rejected_sign_in`.
- `test_a_plain_credential_failure_on_a_local_chat_stays_a_sign_in_failure`.
`tests/gateway/test_model_pick_refused_route.py`:
- `test_pick_leaves_a_route_the_managed_policy_refuses` checks prepared policy
  and switch arguments with a stubbed switch, not a live provider request.
Tests were inspected, not executed for this documentation task.

## Applying

Apply 1353 and 1360 in series order over managed subscription routing.
No new configuration or plugin is required.

## Gotchas

This is not permission to supply a custom proxy or key under managed routing.
The model-pick test does not exercise a Desktop UI or a full mutation commit;
non-local refusal presentation and actual post-switch inference are untested here.
