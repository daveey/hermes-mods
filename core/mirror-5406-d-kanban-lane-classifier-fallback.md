# Kanban lanes: bounded semantic classifier fallback

Mirror snapshot 5406; ordinals 5384, 5385.

Entries:

- **5384**: Classify unmatched lane cards with a bounded subscription fallback
- **5385**: Inventory bounded lane-classifier helper subprocess

## Behavior

- **5384**: cards with no deterministic provenance are classified by a bounded helper subprocess using lane descriptions; explicit/rule/inherited precedence and gating are unchanged; semantic routing is preflighted before write transactions; shadow and nested writes never invoke it.
- **5385**: the helper subprocess is inventoried as a bounded, not drain-fenced probe.

## How it works

- `kanban_lane_classifier.py`, `kanban_lanes.py`, `kanban_db.py`, spawn inventory JSON, docs.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_lane_classifier.py`
- `hermes_cli/kanban_lanes.py`
- `website/docs/user-guide/features/kanban.md`
- `hermes_cli/worker_drain_spawn_inventory.json`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_cli/test_kanban_lane_classifier.py`
- `tests/hermes_cli/test_kanban_lane_classifier_transport.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- A five-second probe at card creation; failures fall back to existing behavior.

## Why core instead of a plugin

Classification happens inside card creation in core.
