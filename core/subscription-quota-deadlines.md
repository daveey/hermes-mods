# Subscription quota exhaustion keeps its reset deadline and the exact pin

Patches:

- `patches/1554-fix-routing-retain-subscription-quota-deadlines-and-.patch` (`d76f44fcd9d`, `fix(routing): retain subscription quota deadlines and exact pins`)
- `patches/1555-fix-kanban-retain-all-typed-quota-deadlines-at-worke.patch` (`747b28718d5`, `fix(kanban): retain all typed quota deadlines at worker boundaries`)
- `patches/1556-fix-routing-retain-authorized-class-fallback-on-quot.patch` (`21acd343ff6`, `fix(routing): retain authorized class fallback on quota exhaustion`)

## Behavior

- When a subscription (OAuth) pool has no usable entry because every live entry carries a known 429/model cooldown, class routing raises a typed `SubscriptionQuotaUnavailable` (a `ModelRoutingError`, `status_code = 429`) with `provider`, `model` and the earliest `resets_at`. Before, this surfaced as a generic routing error or the "Subscription routing requires an OAuth credential, never a paid API key" accusation.
- Only a known cooldown counts as quota evidence. Empty or dead OAuth pools, auth/refresh/billing benches without a 429, and long model-entitlement benches do not produce the typed error. A runtime whose key is not one of the pool's OAuth rows (a foreign or paid key) still gets the plain `ModelRoutingError`.
- In a turn, the typed error is not rotated (rotation would re-bench the last token and lose the original reset). 1556: the agent first tries an authorized class fallback (`_try_activate_fallback`, which keeps the no-paid fence and exact pins); only when none remains does the turn return a rate-limited result with `failure_reason='rate_limit'`, `failure_retryable=True`, `failure_resets_at`.
- A Kanban worker that hits it (at startup, wrapped in another exception, or as a turn result) exits 75 (rate-limited requeue), not 1 or 78. The `worker_result` event keeps an allowlisted, credential-free receipt: `failure_reason`, `failure_retryable`, `failure_resets_at`.
- The dispatcher's respawn guard returns `subscription_quota_reset` until that deadline passes, when the latest ended run is `rate_limited` with exit 75 and a future `failure_resets_at`. This hold is exempt from the guard hold cap. The run does not count as a crash and does not change the lane or consume the retry/crash budget.
- 1555 widens the receipt: turn results with `failure_reason` `upstream_rate_limit` or `billing` and a finite reset, and Codex `AuthError` quota errors with a positive finite `retry_after` (converted to `now + retry_after`), are retained too. `_codex_quota_exhausted_error` now carries `retry_after`.
- Exactly pinned cards are no longer re-tiered off a walled provider by the wall re-tier (`kanban_model_tier` skips wall re-tier for any `pinned*` reason). They keep their model/provider tuple.

## How it works

`agent/model_routing_unavailable.quota_unavailable(pool, model)` scans live OAuth entries; any entry with no future reset means the pool is not exhausted. `resolve_class_runtime` collects these errors while routing filters candidates, and on a final `ModelRoutingError` raises the earliest one only if nothing was available and nothing resolved. `verify_class_runtime` raises it when the pool has no current entry but the runtime key belongs to the pool. `turn_exit.credential_failure_flags` walks the cause chain and maps the typed error to `credentials_rate_limited`. `hermes_cli/kanban_quota_receipt.py` holds `quota_fields` (exception/result to receipt) and `waiting_for_quota_reset` (latest ended run's `worker_result` receipt).

## Code

- `agent/model_routing_unavailable.py` (new), `agent/model_routing_runtime.py`, `agent/turn_api_error.py`
- `hermes_cli/kanban_quota_receipt.py` (new), `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/turn_exit.py`, `hermes_cli/cli_agent_setup_mixin.py`, `hermes_cli/kanban_model_tier.py`, `hermes_cli/auth_codex.py`
- `gateway/session_kanban.py`: `_record_worker_result(..., failure)` on turn, start and late-failure paths

## Tests

- `tests/agent/test_subscription_quota_exhaustion.py`: `test_success_then_pool_exhaustion_retains_quota_and_pin` (stream and non-stream wire: OAuth bearer only, pin kept, no fallback chain, `failure_resets_at`); `test_pinned_startup_preserves_quota_type_and_deadline` (exit 75, also when wrapped); `test_exhaustion_does_not_hide_foreign_runtime_credential`; `test_subscription_deadline_ignores_paid_pool_rows`.
- `tests/gateway/test_subscription_quota_receipt.py::test_quota_receipt_gates_admission_until_reset` (turn and startup paths; guard holds, hold cap is None, released after reset).
- `tests/hermes_cli/test_kanban_credential_startup_exit.py::test_credential_resolution_exit` gains a `subscription` case (exit 75).
- `tests/hermes_cli/test_kanban_provider_walls.py::test_pinned_card_on_walled_provider_keeps_exact_pin`.
- 1555, `tests/hermes_cli/test_kanban_quota_deadlines.py`: `test_turn_reset_survives_real_reap` (ready/review × three reasons), `test_no_unproven_quota_deadline`, `test_wrapped_startup_quota_retains_lane_and_retry_budget` (including a later `sweep_stale_triage`), `test_tier_selected_quota_does_not_consume_crash_budget` (pinned, pattern, retry, standard), `test_codex_startup_known_retry_deadline`, `test_auth_without_valid_quota_wait_has_no_deadline`.
- 1556: untested. The class-fallback activation on `SubscriptionQuotaUnavailable` has no test; the commit only updates `_record_worker_result` stubs in `tests/hermes_cli/test_kanban_worker_start_failure_cause.py`.

This mirror did not run these tests.

## Applying

These patches depend on the preceding series (class routing, managed workers, provider walls, darkwake exit-75 booking). Apply the full ordered series per [README.md](README.md) "Applying". Standalone application is not certified.

## Gotchas

- The guard reads only the latest ended run. A newer run with another outcome supersedes the quota hold.
- A receipt without a finite numeric reset (None, bool, NaN, inf, string) holds nothing; the card falls back to the normal rate-limit cooldown.
- A Codex `retry_after` deadline is computed from the receipt's write time, not from the provider's absolute reset.
- Exact pins now wait out the wall on their own provider instead of moving; see [kanban-provider-quota-walls.md](kanban-provider-quota-walls.md).

## Plugin?

No. The changes sit in class routing, the turn error handler and the dispatcher's respawn guard, which have no plugin seams.
