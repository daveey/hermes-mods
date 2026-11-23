# Per-card eligibility faults and pickup preflight

Source patch ordinals: 5624–5626, 5629, 5632.

## Behavior

Broken profile policy or capability configuration refuses only the affected candidate instead of aborting healthy sibling dispatch/pickup. Unroutable cards and structurally unavailable reviews are rejected before replica import or claim. Active quota episodes cannot skip structural class eligibility.

## How it works

`unroutable_class` contains the entire non-allocating read and reports `model_unroutable:config_unavailable` on faults. It validates provider/policy shapes and construction capability requirements, including review policy enablement. `pickup_refusal` checks the replica read-only and dry-runs review-owned admission before consuming pickup budget. Structural checks precede transient holds. Final tier application rechecks the executing policy and raises `ModelRoutingError` if policy changed after preclaim instead of falling back to a profile/legacy pin. Selector type validation and review exception translation preserve typed ingress refusal.

## Code

- `hermes_cli/kanban_class_walls.py`
- `hermes_cli/kanban_worker_node.py`
- `hermes_cli/kanban_db_dispatch_admission.py`
- `hermes_cli/kanban_model_tier.py`
- `hermes_cli/kanban_review_model.py`
- `hermes_cli/kanban_model_neutral.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/hermes_cli/test_kanban_unroutable_badconfig_review46.py` and `test_kanban_unroutable_pickup_review46.py` cover sibling continuity and zero-import refusal. Eligibility/pickup review49 suites cover nested faults, disabled review routing, recovery, standard fallback and resolver failures. Config/dispatch/ingress review56 suites cover malformed capability context, healthy siblings and structured review/text refusals. `test_kanban_model_neutral_review61.py` checks active-episode recovery and policy drift at final application. Candidate receipt and review-wall tests retain lane/refusal observations.

## Applying

Apply after class-aware and review-owned preclaim admission, including the worker pickup call site and final launch recheck. Repairing a profile policy should restore admission without weakening refusal fences. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

`config_unavailable` is an eligibility failure, not CPU capacity. An existing quota episode owns deadlines, not permission to bypass structure. A failed wall probe must not bypass review structure. Pickup previews allocate neither claims nor reviewer capacity.

## plugin?

No. Core admission/pickup must own these fault and custody boundaries; plugins may surface typed diagnostics.
