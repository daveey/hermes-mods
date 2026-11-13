# Kanban review-release authority: boundary tests and replica-only stamp

Patches: **3175–3180**.

## Behavior
- A review-release receipt carried on a home snapshot crosses the feed and is picked up by a replica; a baseline-guard failure is covered.
- Boundary rules pinned by tests: a fresh replica must match the home episode; a local observation requires a ready boundary; a corrupt receipt is never authority; prepare never evaluates an ineligible owner; promotion rechecks prepared evidence; a checkpoint comment before park releases a partial replica.
- Only a replica honours the home's review-release stamp; the home never honours its own stamp (3179).
- The consumer checks home origin unconditionally (3180) and a provably redundant missing-origin operand is gone (3178). A duplicate producer parent fence was removed (3177).

## How it works
- 3175 extends the transport test so the receipt rides the snapshot feed and pickup.
- 3176 adds boundary suites for portable release authority and observed-PR hold.
- 3177 adds mutation-killing controls (no receipt for a home hold verdict, no proof from a reviewer of another PR, owner fault after park revokes the receipt, malformed checkpoint scope not released) and drops one redundant fence line in `kanban_review_release.py`.
- 3178 and 3180 simplify the consumer condition without changing behavior.
- 3179 makes the consumer ignore the stamp when running on the home.

## Code
- `hermes_cli/kanban_review_release.py`
- `tests/hermes_cli/test_kanban_review_release.py`
- `tests/hermes_cli/test_kanban_review_release_boundaries.py`
- `tests/hermes_cli/test_kanban_review_release_controls.py`
- `tests/hermes_cli/test_kanban_review_release_transport.py`
- `tests/hermes_cli/test_kanban_observed_pr_hold_boundaries.py`
- `tests/hermes_cli/test_kanban_operator_resume_observed.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_review_release_transport.py`: `test_home_review_receipt_crosses_feed_and_pickup`.
- `tests/hermes_cli/test_kanban_review_release_boundaries.py` and `tests/hermes_cli/test_kanban_observed_pr_hold_boundaries.py` (names added across both files): `test_fresh_replica_must_match_home_episode`, `test_local_observation_requires_ready_boundary`, `test_corrupt_receipt_is_not_authority`, `test_lifecycle_and_scope_boundaries`, `test_prepare_never_evaluates_ineligible_owner`, `test_promotion_transaction_rechecks_prepared_evidence`, `test_checkpoint_comment_before_park_releases_partial_replica`, `test_observation_does_not_override_another_local_guard`, `test_home_hold_verdict_emits_no_receipt`, `test_release_without_canonical_head_stays_local`, `test_reviewer_of_another_pr_adds_no_proof`, `test_malformed_checkpoint_scope_emits_no_receipt`, `test_parent_without_carryable_result_adds_no_proof`, `test_partial_replica_checks_proof_fields`, `test_foreign_copy_needs_home_origin_and_portable_park`, `test_malformed_checkpoint_scope_is_not_released`, `test_owner_fault_after_park_revokes_receipt`.
- `tests/hermes_cli/test_kanban_review_release.py`: `test_home_never_honours_its_own_stamp`.
- 3178 and 3180 are refactors with no tests of their own.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
