# Kanban overlap, respawn and priority/help guards

Patches: **3087, 3088, 3096, 3109**.

## Behavior
- `create_overlap` matches titles that name the same code file when they share at least 6 words and 30% of the longer title; distinct-target and work-role exemptions apply and a card citing the owner by id is admitted.
- A PR owner requeued by a core update after publishing gets exactly one continuation instead of stalling in ready.
- Priority migration restore limits are documented and a mixed new/clamped state refuses atomically.
- Hub-reason CLI help matches the admission schema wording (untested).

## How it works
- 3087 extends `kanban_overlap.py`; 3088 adds `kanban_requeued_publisher.continues()` consulted by `active_pr_reason`; 3096 adds docs and an atomic refusal fixture; 3109 copies schema wording into parser help.

## Code
- `hermes_cli/kanban_overlap.py`
- `hermes_cli/kanban_requeued_publisher.py`
- `hermes_cli/kanban_db_dispatch_pr_state.py`
- `hermes_cli/kanban_core_update_requeue.py`
- `hermes_cli/kanban_priority_migration.py`
- `hermes_cli/kanban_parser.py`
- `docs/kanban-priority-rollout.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_create_overlap_shared_file.py`: `test_run_ci_duplicate_from_another_gateway_is_refused_naming_the_hub_card`, `test_shared_file_with_too_few_shared_words_is_not_a_match`, `test_same_words_without_a_shared_file_stay_under_the_half_rule`, `test_a_different_file_of_the_same_kind_is_not_a_match`, `test_two_dotted_files_sharing_only_their_suffix_are_not_a_match`, `test_a_card_that_cites_the_owner_by_id_is_admitted`.
- `tests/hermes_cli/test_kanban_core_update_requeue_handoff.py`: `test_requeued_run_that_published_a_new_pr_gets_one_continuation`, `test_requeued_publisher_resumes_once_and_its_crash_holds`, `test_requeued_publisher_continuation_that_timed_out_holds`, `test_publisher_crash_or_other_reclaim_holds`, `test_publisher_with_failure_evidence_holds`, `test_publisher_of_another_profile_holds`.
- `tests/hermes_cli/test_kanban_priority_executor.py`: `test_mixed_new_and_clamped_old_refuses_atomically`, `test_legacy_flag_after_capped_restore_is_noop`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
