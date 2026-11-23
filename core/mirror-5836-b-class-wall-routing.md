# Class-aware quota holds and profile routing

Source patch ordinals: 5569–5571, 5580, 5587, 5617, 5627.

## Behavior

Known quota walls are checked against eligible class routes before claims. A healthy eligible peer permits dynamic admission; all eligible routes walled hold without creating a run. Launch validation uses the executing assignee's policy, and unrelated provider walls do not replace healthy weighted class allocation.

## How it works

`class_wall_routes` reads the current assignee config per allocation, validates policy, applies provider enablement and construction capabilities, and probes eligible routes without allocating quota. `unroutable_class` reports structural refusal before claim. Resolved classes, including explicit selectors, use class wall checks. Relevant wall evidence may produce a concrete per-run alternate without changing card restrictions; irrelevant provider walls leave selection deferred to weighted routing.

## Code

- `hermes_cli/kanban_class_walls.py`
- `hermes_cli/kanban_db_dispatch_admission.py`
- `hermes_cli/kanban_dispatch_skip.py`
- `hermes_cli/kanban_model_tier.py`
- `hermes_cli/kanban_quota_episode.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/hermes_cli/test_kanban_class_wall_admission.py` covers actual wall-aware admission and sequential assignee registries. `test_kanban_class_unroutable.py` asserts zero runs/claims and typed ineligibility. `test_kanban_dispatch_claim_allowlist.py` covers unavailable profile config. `test_kanban_model_assignment.py` and `test_kanban_requires_declaration.py` cover executing-profile policy. `test_review_class_wall_probe.py` covers explicit/default classes and healthy same-provider peers; `test_kanban_class_wall_weighting.py` checks unrelated-wall weighting and eligible-candidate wall scope.

## Applying

Apply with model-neutral projection and the quota/tier consumers. Include later fault-containment and active-episode structural checks before treating this as the final admission policy. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

Quota probes are not credentials/reserve allocations. A Board/default profile registry does not prove the executing profile is eligible. Model-specific walls must not be broadened into provider-wide bans; later corrections preserve that scope.

## plugin?

No. Claim admission and quota routing are core operations.
