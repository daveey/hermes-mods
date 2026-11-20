# Freshness-bounded model picker reuse

Mirror entries: 4483, 4484, 4519, 4520, 4521, 4522, 4541.

## Behavior

- **4483** — Credential-pool parsing is shared only inside one synchronous picker build while writers remain fresh.
- **4484** — Additional regressions prove next-pass auth visibility and scope cleanup after failures and exits.
- **4519** — Provider-routability probes skip discarded error hints, and read-only config probes avoid unnecessary deep copies.
- **4520** — An intermediate provider-layer optimization memoized bound-home resolution.
- **4521** — That bound-home optimization is reverted; provider-layer home resolution remains fresh.
- **4522** — Regression guards retain expanded-environment and reset/repopulated-home-key freshness cases.
- **4541** — Whole model-options payloads can be reused briefly while all named configuration, credential, plugin and usage inputs remain unchanged.

## How it works

The auth read scope belongs to its creating thread and becomes inactive at exit. Every scoped read revalidates the sampled auth path and version before sharing a parse; returned provider slices are deep copies. Nested scopes share the same pass, but copied contexts cannot extend its lifetime or carry mutable snapshots to another thread. Read-modify-write loaders are not routed through this cache.

Provider name probes answer known/unknown names without constructing error text they will discard, while `auto` still uses real resolution. Config consumers use read-only snapshots but copy values they expose to callers.

The final whole-payload memo keys the context representation, visibility flags and named stamps for home, config, dotenv bytes, local/global auth bytes, environment, catalog files, external credential files, provider registry and account-usage generation. Auth/dotenv content keys detect same-size same-tick rewrites. Entries last less than five seconds, hold at most eight keys and return independent copies. `refresh=True` clears and bypasses the memo; exceptions are not stored. The reverted bound-home memo is not part of the final behavior.

## Code

- `agent/account_usage_cache.py`
- `hermes_cli/auth.py`
- `hermes_cli/auth_plugin_providers.py`
- `hermes_cli/auth_pool_read_scope.py`
- `hermes_cli/inventory.py`
- `hermes_cli/model_catalog.py`
- `hermes_cli/model_switch_providers.py`
- `providers/__init__.py`

## Tests

Source tests exercise parse counts, sampled-path races, per-thread/pass lifetime, returned-object isolation and next-pass updates; provider decision parity and config nonmutation; environment-expanded homes and home-key resets; every payload stamp's invalidation, explicit refresh, flags/context, TTL, exceptions and visibility parity. These are inspected source tests, not new picker benchmarks.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/hermes_cli/test_bare_custom_picker_row.py`
- `tests/hermes_cli/test_bound_home_layer_inputs.py`
- `tests/hermes_cli/test_model_catalog.py`
- `tests/hermes_cli/test_model_options_memo.py`
- `tests/hermes_cli/test_picker_auth_pass_visibility.py`
- `tests/hermes_cli/test_picker_auth_read_scope.py`
- `tests/hermes_cli/test_picker_warm_cost_cuts.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

The five-second TTL bounds unstamped cooldown, pricing and live-probe inputs and re-arms build side effects; it is not an indefinite semantic cache. External credential/catalog stamps use their documented metadata strategy, unlike local/global auth content bytes. Do not describe the reverted bound-home change as a performance improvement still in effect.
