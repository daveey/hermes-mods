# Review completion contracts and model independence

Mirror entries: 4767, 4768, 4808, 4852.

## Behavior

Generated independent-review assignments require verdict, reviewed_sha and reviewed_pr metadata even when the reviewer omits every review key. Review routing excludes all known implementer models, including when quota or pacing changes the route.

## How it works

The completion classifier recognizes the generated brief plus its stated metadata contract while preserving published_pr landing handoffs and ordinary discussion cards. Routing collects concrete non-review run models, preferring worker-result evidence over the same run route receipt, and adds concrete author pins and relevant downstream provenance. Known-author reviews resolve to independent concrete routes. Base resolution, wall/pace rerouting and offer validation apply the full exclusion set; mandatory reviewer pins are not silently moved. Routing receipts retain the concrete handoff and sorted implementer set. The end-to-end rate-limit fixture uses a distinct fake reviewer model.

## Code

- `hermes_cli/kanban_llm_pacing_dispatch.py`
- `hermes_cli/kanban_model_tier.py`
- `hermes_cli/kanban_paced_reroute.py`
- `hermes_cli/kanban_review_contract.py`
- `hermes_cli/kanban_review_model.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Review-contract tests cover refusal without metadata, same-run correction and landing exemptions. Implementer-set/reroute tests cover multiple authors, missing provenance, mandatory pins, held or walled peers, unsafe pace offers and requeue rather than spawn. The end-to-end fixture asserts the distinct route at its fake provider.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/e2e/core/kanban/test_kanban_rate_limit_review.py`
- `tests/hermes_cli/test_kanban_class_routing.py`
- `tests/hermes_cli/test_kanban_llm_pacing.py`
- `tests/hermes_cli/test_kanban_managed_pin_policy.py`
- `tests/hermes_cli/test_kanban_model_tier_paced_reroute.py`
- `tests/hermes_cli/test_kanban_pin_reroute.py`
- `tests/hermes_cli/test_kanban_quota_deadlines.py`
- `tests/hermes_cli/test_kanban_review_handoff_validation.py`
- `tests/hermes_cli/test_kanban_review_implementer_set.py`
- `tests/hermes_cli/test_kanban_review_model.py`
- `tests/hermes_cli/test_kanban_review_paced_reroute.py`
- `tests/tools/test_kanban_review_contract.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

Unknown implementer history retains legacy behavior; independence is only as strong as available provenance. An explicit reviewer pin does not authorize reviewing its own implementation. Fixture updates do not change production policy.
