# PR acceptance: pre-merge checks vs main CI

Patches: **1725** (`df7d36c08b`, `fix(kanban): separate pre-merge acceptance from main CI [daveey]`); **1726** (`8ecc28347d`, `fix(kanban): keep skipped post-merge jobs out of red main notice [daveey]`); **1727** (`c3ebcfc158`, `fix(kanban): retain pending-at-merge check outcomes [daveey]`); **1745** (`b854715306`, `[daveey] Pin 3 untested merge-time PR acceptance guarantees from #682`); **1782** (`cd592994bc`, `[daveey] fix: prove OPEN check-run evidence complete per check suite`); **1802** (`f56940d772`, `[daveey] fix(kanban): same-second post-merge run no longer pins MERGED acceptance pending`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- PR acceptance separates pre-merge acceptance from main-branch CI (1725); skipped post-merge jobs stay out of the red-main notice (1726); checks pending at merge keep their outcomes (1727).
- Three merge-time guards gain tests: app identity in `partition_at_merge`, a REST/GraphQL merged-state disagreement classified stale, all-naive timestamps fail closed (1745).
- OPEN check-run evidence is proven complete per check suite, so a capped or racing `filter=latest` view cannot pass (1782).
- A post-merge run that started in the merge's own second no longer pins MERGED acceptance pending (1802).

## How it works

`kanban_pr_acceptance`, `kanban_pr_merge_time`, `kanban_pr_check_runs`; `tests/hermes_cli/check_suite_fake.py` models suites, attempts and empty reruns.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1725: (subject only)
- 1726: (subject only)
- 1727: (subject only)
- 1745: Mutation check of #682 found three guards no test covered: - partition_at_merge app identity when another app's failed run is OLDER than a same-named success (name-only key survived) - REST merged flag disagreeing with the earlier GraphQL state (PR merged mid-collection) must classify stale - all-naive timestamps must fail closed as infra (only mixed naive/aware was covered, via comparison TypeError)
- 1782: ## PR acceptance: complete check-run evidence (OPEN latest view proven per suite)
- 1802: mergedAt has 1 s resolution. A post-merge push run that started in the merge's own second and finished after it was read as a pre-merge run still in progress at merge and, as latest by start, overrode the earlier pre-merge success: the merged card stayed pending forever.

## Code

`hermes_cli/kanban_pr_acceptance.py`, `hermes_cli/kanban_pr_acceptance_store.py`, `hermes_cli/kanban_pr_check_runs.py`, `hermes_cli/kanban_pr_merge_time.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/check_suite_fake.py`
- `tests/hermes_cli/fixtures/pr656_check_runs.json`
- `tests/hermes_cli/test_kanban_main_health_owner.py`: `test_first_red_streak_owner_across_boards`, `test_only_active_owner_is_annotated_without_writes`, `test_owner_lookup_failure_does_not_change_acceptance`, `test_sibling_open_is_read_only_and_missing_db_not_created`
- `tests/hermes_cli/test_kanban_pr_acceptance.py`
- `tests/hermes_cli/test_kanban_pr_enumeration.py`: `test_absent_zero_count_suites_pass_up_to_the_suite_limit`, `test_incomplete_malformed_or_racing_evidence_fails_closed`, `test_latest_selection_cannot_hide_strict_evidence`, `test_latest_view_capped_below_suite_count_fails_closed`, `test_latest_view_past_one_page_is_paginated`, `test_main_health_requires_proven_landed_head`, `test_open_retains_failed_run_of_another_suite`, `test_recorded_commit_latest_view_is_each_suites_latest_attempt`, `test_recorded_failure_retained_while_its_attempt_is_latest`, `test_recorded_superseded_runs_in_latest_view_fail_closed`, `test_rerun_attempt_supersedes_the_earlier_attempt`, `test_same_second_red_start_follows_earlier_conclusion` (+3 more)
- `tests/hermes_cli/test_kanban_pr_merge_time.py`: `test_all_naive_merge_chronology_fails_closed`, `test_completion_receipt_names_main_health_owner`, `test_failed_premerge_rerun_overrides_earlier_success`, `test_later_success_never_repairs_premerge_evidence`, `test_legacy_latest_premerge_failure_wins`, `test_legacy_statuses_obey_merge_cutoff`, `test_merge_selection_preserves_evidence_guards`, `test_merged_fast_forward_uses_premerge_success`, `test_nonfailure_postmerge_checks_do_not_claim_main_is_red`, `test_open_still_uses_latest_without_cutoff`, `test_open_uses_latest_view_without_cutoff`, `test_pinned_app_cannot_borrow_legacy_success` (+10 more)

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- `mergedAt` has one-second resolution; the tie rule needs an earlier same-key run.

## Plugin?

No: these change core modules that plugins cannot hook.
