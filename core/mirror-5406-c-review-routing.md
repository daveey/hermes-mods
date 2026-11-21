# Host-local review walls and bounded author epochs

Mirror entries: 5231, 5232, 5233, 5234, 5235, 5313, 5314, 5315. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Independent-review cards with proven temporary reviewer exhaustion are deferred on this host before claim, without spending run/failure budgets or changing status. Another host with an available reviewer may claim them. Pickup avoids copying locally walled reviews that would consume the pickup budget and churn.

Reviewer author exclusions track the current implementation epoch rather than accumulating every historical author forever. A review verdict followed by concrete new implementation evidence can establish a new epoch; unknown implementation authors retain conservative history/pins. Structural absence of an independent reviewer is distinct from a timed provider wall.

## How it works

`ReviewRouteDeferred` carries the earliest provable available route deadline: each route needs the maximum of its overlapping finite future walls/holds; the resolver chooses the minimum across eligible routes. Legacy boolean walls, nonfinite resets and unknown/elapsed pacing membership cannot establish a deadline. The exception retains its type through fallback/tier wrapping. Preflight errors fall back to normal per-card spawn accounting rather than aborting sibling dispatch.

A dry-run twin predicts pickup holds. Deferral events are deduplicated by reason and integer deadline, and dispatch-skip output explains the local wait. Author scanning recognizes review-route events and pre-resolution review claims (`claimed.source_status=review`). Only `completed` or `changes_requested` review outcomes establish verdict boundaries; parked/crashed/retried reviews and missing receipts do not erase earlier authors. `ReviewModelsExhausted` remains typed and books a structural infrastructure refusal rather than an endless spawn loop.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/kanban_db_dispatch_admission.py`
- `hermes_cli/kanban_dispatch_skip.py`
- `hermes_cli/kanban_model_tier.py`
- `hermes_cli/kanban_review_admission.py`
- `hermes_cli/kanban_review_model.py`
- `hermes_cli/kanban_worker_node.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Wall-admission tests cover walled/unwalled hosts, standard fallback, finite-deadline boundaries, unknown pacing reset, resolver-error isolation, suppression evidence, bounded repeated events, and dispatch-skip reporting. Pickup tests check that walled reviews do not starve another card, unwalled reviews copy normally and failed predictions copy as before. Review-cycle tests cover repeated verdict cycles, mixed known/unknown author epochs, missing receipts, pre-spawn review refusals without routes, current-author exclusion, provider-wall versus structural exhaustion, and dispatcher behavior after four real verdict cycles.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_kanban_managed_pin_policy.py`
- `tests/hermes_cli/test_kanban_pickup_review_wall.py`
- `tests/hermes_cli/test_kanban_review_model.py`
- `tests/hermes_cli/test_kanban_review_model_cycles.py`
- `tests/hermes_cli/test_kanban_review_wall_admission.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

Pickup prediction deliberately fails open on errors; this is not permission to bypass authoritative spawn policy. A timed wall is local availability, not permission to choose an author model. A launch or result receipt alone is not a review verdict. Epoch history uses local run/event relationships; these changes do not independently certify arbitrary replica ordering.

## Why core instead of a plugin

The resolver, dispatch preclaim gate, pickup budget and failure accounting share core state. Plugins can supply policy, but cannot make these admission and provenance boundaries consistent outside their owners.
