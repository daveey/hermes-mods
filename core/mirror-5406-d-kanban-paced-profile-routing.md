# Kanban pacing: profile defaults route through their own peers

Mirror snapshot 5406; ordinals 5388, 5389.

Entries:

- **5388**: fix(kanban): route paced profile defaults through their own peers
- **5389**: fix(kanban): preserve recorded pacing on live and replica card reads

## Behavior

- **5388**: offers bind to the effective launch route; eligible providers come from the assignee's class registry; explicit pins keep their reserve hold; the latest pacing skip is read before placement.
- **5389**: card reads overlay recorded pacing without evaluating a replica against the reader's capacity.

## How it works

- `kanban_llm_pacing_dispatch.py`, `kanban_paced_reroute.py`, `kanban_dispatch_skip.py`, `kanban_model_tier.py`, `kanban_queue_position.py`, dashboard API.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_dispatch_skip.py`
- `hermes_cli/kanban_llm_pacing_dispatch.py`
- `hermes_cli/kanban_model_tier.py`
- `hermes_cli/kanban_paced_reroute.py`
- `hermes_cli/kanban_queue_position.py`
- `plugins/kanban/dashboard/plugin_api.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_cli/test_kanban_model_tier_paced_reroute.py`
- `tests/hermes_cli/test_kanban_pace_reroute.py`
- `tests/hermes_cli/test_kanban_pacing_queue.py`
- `tests/hermes_cli/test_kanban_profile_pace.py`
- `tests/plugins/test_kanban_pacing_detail.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- No reserves, budgets or priorities change.

## Why core instead of a plugin

Dispatch pacing is core.
