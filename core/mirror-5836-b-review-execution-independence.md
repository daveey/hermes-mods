# Independent review by separate execution

Source patch ordinals: 5613–5614.

## Behavior

Independent review requires genuine separate execution/session and exact-head approval custody, not a different model identity. The same eligible model may review in a separate execution. Authenticated model attribution remains audit provenance.

## How it works

Prompt and recovery guidance retire the older model-exclusion wording. `independent_routes` no longer injects a concrete `strong_model`; candidates are ordered by the profile registry's class/capability policy, including same-model candidates. Concrete historical overrides are ignored, new concrete overrides refused, and valid class requirements retain their eligibility gates.

## Code

- `agent/prompt_builder.py`
- `hermes_cli/kanban_resume_requester.py`
- `hermes_cli/kanban_review_model.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/hermes_cli/test_kanban_model_neutral_text.py` checks shipped guidance. Review model/retirement, implementer-set, source-replica, tier-fallback, wall-admission and declared-implementer suites reconcile registry-only candidates, same-model eligibility, unchanged provenance and refusal behavior. `test_kanban_model_assignment.py`, `test_kanban_model_tier.py` and quota episode suites align downstream consumers.

## Applying

Apply with the model-neutral review family. This correction supersedes early text claiming every author model must be excluded; retain existing separate-execution and exact-head custody checks. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

A different model alone is not independent approval. This patch does not authorize self-review in the same execution, waive exact-head CI/approval, or treat model attribution as execution custody. Missing eligible routes still fail closed.

## plugin?

No. Candidate selection and approval custody are core review contracts.
