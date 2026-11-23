# Restricted terminal parents remain dependency blockers

Patch coverage: **5849–5850, 5855, 5858**.

## Behavior

A parent being `done` or `archived` is not enough to release its children when execution restrictions or containment still apply. Ordinary successful terminal parents retain their normal behavior. Readiness recomputation also leaves a stopped card held even when its raw status is stale.

## How it works

`parent_blocks` centralizes terminal restriction checks and exposes explicit `:restricted` blocker labels. Full and incremental foreign-parent refreshes freeze restriction evidence into status mirrors without importing the parent card. Worker candidates use the same parent-satisfaction predicate. Selected-source lanes read each parent's restriction from the source that owns the selected copy rather than its raw status. The schema-aware terminal projection tolerates legacy tables lacking containment/history columns; where present it treats containment archives and live safety/scope stop evidence as non-success. Readiness recomputation checks pending decisions regardless of the current task status.

## Code

- `hermes_cli/kanban_db.py`, `kanban_db_parent_states.py`, `kanban_containment_terminal.py`: readiness, linking and effective terminal state.
- `hermes_cli/{kanban_db_sync,kanban_sync_parent_refresh,kanban_worker_node,kanban_lanes_sources}.py`: foreign mirrors, workers and selected-source flow.

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live deployment or performance result is claimed.

- `tests/hermes_cli/test_kanban_parent_restrictions.py`: link/recompute/read blockers, worker candidates and same-status restriction changes under full/incremental foreign refresh.
- `tests/hermes_cli/test_kanban_containment_terminal_parents.py`: legacy mirror schemas.
- `tests/hermes_cli/test_kanban_restricted_parent.py`: selected-source lanes, ordinary terminal success, containment archives and supported renewal.
- `tests/hermes_cli/test_kanban_actionability_dependencies.py`: archived stopped parents, stale todo/blocked targets, ordinary parent release exactly once and foreign status projections.

## Applying

Apply the covered patches in the full ordered core series using the [core applying recipe](README.md#applying), including its replay bridges and required repair overlay. These entries depend on earlier core changes; they are not advertised as an independent cherry-pick set. Reimplementation should preserve the invariants above and run the named source suites in a disposable source checkout.

## Gotchas

Administrative archive is not stop clearance; do not delete dependency edges to force readiness. These ordinals precede later renewal-specific refinements, so apply the complete series rather than treating historical stop-query behavior as the final supported release implementation.

## plugin?

No: dependency gating must agree across database transitions, worker admission, mirrors and lanes.
