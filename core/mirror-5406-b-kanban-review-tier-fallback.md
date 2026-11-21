# Kanban review: fall back to an independent standard-tier route when every deep route is unavailable or authored the work

Mirror entries 5187.

## Behavior

- When every deep review route authored the implementation or is unavailable, review model selection now picks the first eligible independent standard-tier route instead of raising `ReviewModelUnavailable`. Deep remains preferred unless its pool is unavailable; the standard policy order skips unavailable routes.
- Mandatory reviewer pins and the refused-override deep-only recovery are preserved; an excluded mandatory pin does not fall back; with no independent standard route available it still raises.
- The fallback is recorded as `review_tier_fallback` in both dispatch route receipts.

## How it works

- `hermes_cli/kanban_review_model.py` only (receipts use existing dispatch receipt fields).

## Code

- `hermes_cli/kanban_review_model.py`

## Tests

Source tests (not executed in this mirror pass): `tests/hermes_cli/test_kanban_review_tier_fallback.py` (`test_all_deep_authors_fall_back_to_independent_standard`, `test_no_independent_available_standard_still_raises`, `test_excluded_mandatory_pin_does_not_fall_back`, `test_standard_policy_order_skips_unavailable_routes`, `test_deep_preferred_unless_its_pool_unavailable`, `test_dispatch_records_fallback_in_both_route_receipts`, `test_refused_override_retains_deep_only_recovery`); `test_kanban_managed_pin_policy.py` updated.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Independence (author exclusion) is never relaxed, only the tier.

## Why core instead of a plugin

Reviewer routing is core dispatch policy applied at claim time.
