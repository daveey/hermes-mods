# Kanban: provider quota walls

Patch **0463** (source `eb215058a7`, picked from `8ae2a621a9`).

## Behavior

When a worker exits rate-limited, the provider's `retry after Ns` (largest in the log tail) is recorded as a wall
(minimum 15 min, default 30 min). A card whose tier would run on a walled provider is re-tiered to
`kanban.default_model` / `default_provider`, including keep-pattern, OOM and pinned cards (`require_model` marker).
More than 10 rate-limited runs in an hour open one storm-alert card per host per day.

## Code

`hermes_cli/kanban_provider_walls.py`, `kanban_model_tier.py`, `kanban_db_dispatch.py`.

## Tests

`tests/hermes_cli/test_kanban_provider_walls.py`.

## Applying

After 0460; builds on [kanban-default-provider.md](kanban-default-provider.md).

## Gotchas

Walls are stored in a local file per host, not shared between hosts.

## Plugin?

No.

## Follow-up patches (1578–1579): default-tier fallback and live pool walls

**Patches:** `patches/1578-fix-kanban-route-default-tier-cards-off-a-quota-wall.patch` (`85ea05171a7`, `fix(kanban): route default-tier cards off a quota-walled default provider`); `patches/1579-fix-kanban-log-the-wall-probe-s-swallowed-errors-wit.patch` (`1c96fe663f1`, `fix(kanban): log the wall probe's swallowed errors with traceback`).

**Behavior.** When `kanban.default_provider` is itself the walled provider, the re-tier had no other fallback and never moved a card. `retier_for_walls` now tries the default tier, then the strong tier (`kanban.strong_model` / `strong_provider`), skipping any fallback that is the walled provider, has a recorded wall, or whose own OAuth pool is exhausted now. If every fallback is exhausted the card stays on its route. A provider also counts as walled when `live_quota_wall` finds its OAuth pool quota-exhausted for the card's model at claim time, so no worker has to crash first to record a wall. Cards with `require_model` are left alone (checked first). Exact pins keep their tuple (1554 already skips wall re-tier for pinned cards).

**How it works / Code.** `hermes_cli/kanban_provider_walls.py`: `live_quota_wall(provider, model)` calls `load_pool(provider, allowed_auth_types={"oauth"})` and `agent.model_routing_unavailable.quota_unavailable` (the same evidence the startup resolver raises `SubscriptionQuotaUnavailable` on) and returns the reset time or None; it returns None for `auto`, an empty provider/model or a `class:` selector. `profile_model()` reads `model.default` for unpinned cards. Any pool or config read error returns None so dispatch never breaks; 1579 logs these at debug with a traceback (`exc_info=True`).

**Tests.** `tests/hermes_cli/test_kanban_provider_walls.py`: `test_default_tier_on_walled_default_provider_moves_to_strong_tier`, `test_live_pool_exhaustion_counts_as_wall_without_a_crash` (also asserts the pinned card keeps its tuple), `test_every_fallback_exhausted_leaves_task_on_its_route`, `test_live_quota_wall_reads_the_oauth_pool`. 1579's logging change is untested. This mirror did not run these tests.

**Applying / Gotchas.** Depends on 1554 (`quota_unavailable`); apply the full ordered series per [README.md](README.md#applying), standalone not certified. The live check reads this host's OAuth pool store only. A paid-key provider never shows a live wall. See [subscription-quota-deadlines.md](subscription-quota-deadlines.md).
