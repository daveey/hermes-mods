# Review completion contracts and execution-neutral Board grants

Patches: **2772, 2775, 2777**.

Related context: [kanban-ship-review-completion-gate-2726.md](kanban-ship-review-completion-gate-2726.md), [kanban-dependency-review-continuation.md](kanban-dependency-review-continuation.md).

## Behavior

A review of a core PR cannot evade `kanban_complete`'s structured review contract just because the brief called the PR key `pr` rather than `reviewed_pr`. Verdictless landing/cleanup handoffs remain distinct from reviews. Unused Board grants and core-update requeues no longer consume same-second dependency checkpoints, reset cooldowns, or create a second release of the same park. Tests also cover operator resume reaching kept/fresh worker copies.

## How it works

2777 keeps `is_review_result` and the existing problem validator, but gates either an explicit contract or a body naming a core-fork pull. The core-pull-only branch additionally requires a verdict key (`verdict`, review aliases, `approved` or `approval`) so a verdictless handoff stays ungated. Required output remains `verdict` (`approve`/`request_changes`), lowercase full 40-hex `reviewed_sha`, and exact `reviewed_pr`; `published_pr` landing handoffs stay exempt. 2775 centralizes latest-run selection in `kanban_core_update_requeue`: only attested unused `released` grants and verified update `reclaimed` pauses are skipped. Both latest-run and latest-ended-run readers are used by checkpoint, review continuation/notice, hold-release and ordinary cooldown guards; the latter can order by ended time then ID. A run that started, failed or lacks the unused-grant receipt still counts. Replica matching uses the run's stable identity. 2772 adds home-resume tests for already-kept copies and fresh imports matched by run UID.

## Code

- `hermes_cli/kanban_core_update_requeue.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_pr_state.py`
- `hermes_cli/kanban_dependency_review.py`
- `hermes_cli/kanban_dependency_review_notice.py`
- `hermes_cli/kanban_hold_release.py`
- `hermes_cli/kanban_review_contract.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_dependency_review_unused_grant.py`: `test_hold_release_refuses_a_second_release_for_the_park_across_a_grant`, `test_replica_card_skips_a_grant_by_its_own_run_identity`, `test_pickers_return_none_when_only_unused_grants_ran`, `test_unused_grant_keeps_the_same_second_checkpoint`, `test_started_run_after_a_grant_consumes_the_checkpoint`, `test_unused_grant_does_not_end_a_cooldown_early`, `test_core_update_requeue_does_not_end_a_cooldown_early`, `test_runs_after_a_grant_still_count_for_the_cooldown`.
- `tests/hermes_cli/test_kanban_dependency_unused_grant.py` (fixture/assertion changes; no test definition name appears in this diff).
- `tests/hermes_cli/test_kanban_operator_resume_worker_copy.py`: `test_home_resume_releases_the_workers_kept_copy_at_its_next_tick`, `test_without_a_resume_the_kept_copy_stays_held`, `test_home_resume_releases_a_fresh_import_by_run_uid`.
- `tests/tools/test_kanban_review_contract.py`: `test_body_asking_for_pr_key_is_still_gated`, `test_body_asking_for_pr_key_accepts_reviewed_pr`, `test_body_without_core_pull_is_not_gated`, `test_landing_handoff_on_core_pull_card_is_not_gated`, `test_no_review_keys_on_core_pull_card_is_not_gated`, `test_verdictless_handoff_on_core_pull_card_is_not_gated`, `test_verdictless_handoff_on_contract_card_is_still_gated`, `test_tool_refuses_pr_key_review_then_accepts_reviewed_pr`, `test_tool_leaves_cards_without_contract_alone`.

Coverage is limited to the entry points and fixture controls above; live services, deployment and user-interface behavior are not established by this documentation task.

## Applying

Use the [README full replay procedure](README.md#applying). Apply these patches in published ordinal order with the intervening series, replay bridges and mandatory post-replay repair described there. They are incremental changes, not independently certified cherry-picks. This document does not claim that the full mirror replay or feature suites ran during the documentation pass.

## Gotchas

The earlier completion-gate document's claim that omitting contract key names avoids gating is superseded for a core-pull review carrying a verdict. Other repositories retain their own contracts. This does not relax ship's exact-head consumer. Verdictless metadata on an explicitly contracted card is still checked. Unused grants are not permission to pretend an executed run never happened. CLI/dashboard completion parity and live ship execution are untested here.

## Plugin status

Core/CI implementation; no plugin is added or required. A plugin cannot replace the shared database, admission, worker or workflow invariants documented here.
