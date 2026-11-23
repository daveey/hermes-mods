# Retire reviewer model-diversity gates (mirror 5458–5460)

## Behavior
- Equal author/reviewer model, or missing author-model attribution, is no longer a reason to refuse admission, reroute or reserve models for a Kanban review.
- Still enforced in the touched code: mandatory reviewer pins never move (a mandatory pin that authored the work is now used as-is), held or walled provider pools are skipped or block the route, an unconfigured reviewer route still refuses rather than spawning, and a review override on a held pool still moves. Source comments say separate execution is enforced by verdict custody rather than model identity; the receipt payload now records `review_independence = "separate_execution"` instead of `implementer_model(s)`. Reviewer-run custody, exact-head approval and required-CI code are not in this diff and are not verified here.
- Wall/pace/offer consumers do not reserve author models. `implementer_models` collection remains (the `independent_routes` docstring says attribution is diagnostic only) but is no longer used as a routing exclusion; `validate_pace_offer` is kept as a compatibility hook that no longer refuses on model equality. Legacy no-policy class/profile allocation stays available.

## How it works
The criterion is removed at every consumer: `kanban_review_model.py` (runtime resolver), `kanban_model_tier.py`, `kanban_paced_reroute.py`, `kanban_llm_pacing_dispatch.py`.

## Code
`hermes_cli/kanban_review_model.py`, `kanban_model_tier.py`, `kanban_paced_reroute.py`, `kanban_llm_pacing_dispatch.py`.

## Tests
Source: new `test_kanban_review_model_retirement.py` (same author model is a candidate; missing author model does not deny downstream review; same-model handoff override and pace offer allowed; author models do not filter candidates; missing provenance is not read for admission; provider walls still respected; paced fallback can use the author model), plus new or changed cases in the class-unroutable, managed-pin policy, model-tier paced reroute, pin reroute, declared-implementers, implementer-set, review model (+cycles), review paced reroute, pin admission, replica source, tier fallback and artifact probe tests; 5459 and 5460 touch only the quota-episode and mandatory-pin tests. Not executed in this mirror pass.

## Applying
Apply the three patches together; the later two only adjust fixtures/tests.

## Gotchas
Policy change: same-model review is permitted. Per the source comments, independence now rests on separate execution and verdict custody rather than model identity; this diff does not modify that custody.

## plugin?
No.
