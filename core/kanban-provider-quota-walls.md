# Kanban: provider quota walls

Patch **0457** (source `eb215058a7`, picked from `8ae2a621a9`).

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

After 0454; builds on [kanban-default-provider.md](kanban-default-provider.md).

## Gotchas

Walls are stored in a local file per host, not shared between hosts.

## Plugin?

No.
