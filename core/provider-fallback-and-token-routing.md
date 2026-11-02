# Provider fallback failures and token-unit model routing

Patches: 0486 0487 0511 0527 1625.

Original sources: **0486**, **0487** (`1164b8813f`, `582fab6bfb`) and **0511**, **0527** (`d83286a1bd`, `d56b621070`).

- `patches/0486-fix-providers-preserve-fallback-failures-and-bound-q.patch`
- `patches/0487-refactor-providers-keep-fallback-diagnostics-within-.patch`
- `patches/0511-fix-model_routing-compare-token-estimate-not-UTF-8-b.patch`
- `patches/0527-fix-model-routing-aux-class-requirements-use-token-u.patch`
- `patches/1625-daveey-cron-quota-hold-parks-jobs-on-the-routing-poo.patch`

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

- **1625 (subscription quota hold):** Cron recognizes `SubscriptionQuotaUnavailable` with a future `resets_at`, including a refusal wrapped as a `RuntimeError` cause. A scheduler tick records the failed run, parks `next_run_at` and `quota_hold_until` at or beyond reset, excludes the job from due work and delivers one failure notice saying it is held (tested with injected provider refusal and captured delivery, not a live provider or messaging service).
- **1625:** A shared typed mapping handles 429 `ModelRoutingError` deadlines and rate-limited `AuthError` wait hints. An all-quota fallback chain uses its earliest usable wait; adding a non-quota route prevents a hold. Past or invalid resets and quota-looking text alone do not park jobs.
- **1625:** A recognized quota failure without a usable wait emits a `quota failure not held` warning through cron's classifier; an ordinary runtime error does not. The new test checks warning presence, not an exact warning count.

## How it works

`hermes_cli/fallback_errors.py` and `cron/quota_hold.py` classify chains; `agent/agent_init.py`,
`agent/agent_init_routes.py`, `gateway/run_turn_{runner,resolution_errors}.py` and `tui_gateway/server.py` surface
them; `agent/model_routing_request.py` and `agent/auxiliary_class_routing.py` use the compression/preflight rough
token estimator.

**1625:** `agent/model_routing_unavailable.quota_wait_seconds` walks exception causes/contexts to the first typed refusal or fallback chain. It validates positive finite numeric waits and uses absolute `resets_at` before relative `retry_after`; the text wait hint is accepted only on a typed auth refusal. Fallback classification stops at the chain rather than mistaking its primary exception's context for the whole chain. `cron/quota_hold.hold_seconds_from_failure` delegates to this mapping; `classify_failure_hold` adds the warning, and `cron/scheduler.py` calls it before existing hold bookkeeping.

## Code

Files above, plus `cron/scheduler.py`, `hermes_cli/runtime_provider.py`, `hermes_cli/cli_agent_setup_mixin.py`.

**1625:** `agent/model_routing_unavailable.py`, `cron/quota_hold.py`, `cron/scheduler.py`, and new `tests/cron/test_quota_hold_subscription.py`.

## Tests

`tests/agent/test_init_fallback_refusal_warning.py`, `tests/cron/test_fallback_failure_details.py`,
`tests/gateway/test_fallback_failure_details.py`, `tests/hermes_cli/test_fallback_error_classification.py`,
`test_resolution_failure_details.py`, `tests/tui_gateway/test_fallback_failure_details.py` (cron, gateway, TUI and CLI
entry paths each have a test); `tests/agent/test_model_routing_request_context.py`,
`test_auxiliary_class_requirements.py` (bytes over cap but tokens under cap passes; genuinely oversized still fails).

### 1625 coverage

`tests/cron/test_quota_hold_subscription.py`:

- `test_runtime_error_from_subscription_quota_holds_until_reset`
- `test_passed_or_bogus_reset_gives_no_hold` (past, None, string, NaN, infinity and untyped text)
- `test_fallback_chain_of_pool_refusals_holds_for_the_earliest_reset` (also mixed non-quota refusal)
- `test_every_quota_routing_error_maps_to_a_cron_hold` (discovers 429 routing subclasses, requires a factory and mapping for each; also checks typed auth `retry_after`)
- `test_scheduler_tick_parks_job_on_pool_refusal` (scheduler bookkeeping and captured hold notice)
- `test_quota_failure_without_a_hold_logs_one_warning` (quota warning present, ordinary error warning absent)

Untested:
- Live provider exhaustion, live messaging delivery and recovery after the subscription reset are not exercised by the new scheduler test.
- Exact warning count, cyclic cause chains, nested fallback chains and malformed numeric edge cases beyond the listed fixtures have no dedicated tests in 1625.

These were not rerun by the mirror pass.

## Applying

`git am -3` in number order. 0487 depends on 0486.

**1625** builds on the existing cron hold bookkeeping and the typed routing refusal introduced in 1554; apply it as part of the complete ordered series, not as a standalone patch. No new configuration is added. See [subscription-quota-deadlines.md](subscription-quota-deadlines.md) for the routing refusal and worker deadline receipts.

## Gotchas

The estimator is rough: caps are not a hard tokenizer guarantee, so oversize requests still fail closed.

## Plugin?

No: provider resolution and routing are core.
