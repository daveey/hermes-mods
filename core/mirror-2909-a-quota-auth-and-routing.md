# Quota evidence, explicit release and safe alternate routes

Patches: **2734, 2751, 2752, 2753, 2756, 2760, 2765, 2776**.

Related context: [kanban-quota-episodes.md](kanban-quota-episodes.md), [kanban-quota-recovery.md](kanban-quota-recovery.md).

## Behavior

Authentication benches are not quota evidence: a revoked-401 requeue must not inherit a sibling account's weekly quota deadline. Hinted episode walls disproved by the local serving pool shrink to their required exponential backoff. Operators can explicitly end a stale quota episode. Unpinned strong class routes and independent-review/nonmandatory pin routes can avoid known provider walls under their respective eligibility rules.

## How it works

2734 adds reset-custody coverage for exit-75 auth-transient receipts (`failure_resets_at` and `receipt_deadline` remain empty). 2751 makes `_quota_scan` distinguish room, quota waits and unproven auth benches: an auth-benched live OAuth entry without its own model cooldown prevents claiming all entries are quota-walled. Dead/entitlement benches remain excluded. `live_pool_serving` requires no typed quota refusal plus positive pool availability and fails closed on reads. 2752 records/reconstructs `backoff_until`; disproving a hint never erases doubling pacing (two-hour cap). 2753 treats a provider-file wall ending no later than the hint as a possible mirror, preserves later independent evidence, mirrors only unhinted backoff on reap, and prunes provider walls longer than the backoff cap only when positively disproved. Dry-run skips pruning. 2756 binds `bench_token_fp` to `bench_status_at` matching `last_status_at` within 1 ms, so stale extra data after a later terminal refresh failure cannot falsely turn an all-dead pool into endless exit 75 instead of 78. 2760 adds operator-only `hermes kanban quota-release <id> [--reason ...]` on the card's home gateway. Its event/comment close the episode through the recorded run, clear quota error state and eligible mirrored provider walls, and annotate queue notes until the next run. Worker lineage and foreign copies refuse. 2765 extends non-allocating class alternate eligibility to unpinned `retry_after_*`, `pattern:*` and legacy `review` strong routes, requiring proven local live-pool service at the same class strength. 2776 moves `review_unpinned` to an unwalled independent strong/deep reviewer before spawn, and non-fresh optional pins can use the same known-wall rule as fresh ones. Mandatory model markers/columns, near walls, walled peers, unknown provider custody and active-episode precedence keep their protections.

## Code

- `agent/credential_pool.py`
- `agent/credential_pool_pushed.py`
- `agent/model_routing_unavailable.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_clock.py`
- `hermes_cli/kanban_model_tier.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_pin_reroute.py`
- `hermes_cli/kanban_provider_walls.py`
- `hermes_cli/kanban_queue_position.py`
- `hermes_cli/kanban_quota_episode.py`
- `hermes_cli/kanban_quota_episode_release.py`
- `hermes_cli/kanban_review_model.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_quota_unavailable_auth_bench.py`: `test_auth_benched_accounts_with_quota_do_not_make_a_wall`, `test_auth_bench_on_an_account_with_its_own_quota_cooldown_still_counts_as_walled`, `test_all_429_walled_pool_still_reports_the_earliest_reset`, `test_dead_and_entitlement_benched_entries_stay_excluded`.
- `tests/hermes_cli/test_kanban_pin_reroute.py`: `test_pin_walled_only_by_another_cards_wall_holds`, `test_non_fresh_pin_on_a_known_wall_reroutes_before_its_next_spawn`, `test_non_fresh_pin_on_a_known_wall_controls_never_move`, `test_review_unpinned_on_a_walled_provider_moves_to_a_free_independent_reviewer`, `test_review_unpinned_keeps_its_route_without_a_free_alternative`, `test_create_flag_requires_a_pin_and_persists`.
- `tests/hermes_cli/test_kanban_pool_auth_wall.py`: `test_revoked_requeue_carries_no_quota_reset_of_the_benched_siblings`, `test_refresh_dead_rows_with_a_stale_bench_fingerprint_stay_terminal`, `test_bench_kept_across_a_newer_token_still_reads_transient`, `test_every_account_revoked_after_a_reread_stays_terminal`.
- `tests/hermes_cli/test_kanban_quota_episode_class_strong.py`: `test_retry_card_on_class_route_takes_the_free_provider_of_its_class`, `test_retry_card_keeps_its_hold_while_every_class_route_is_closed`, `test_pattern_strong_class_card_takes_the_free_provider`, `test_unpinned_class_review_takes_the_free_provider_but_a_chosen_reviewer_waits`, `test_only_unpinned_strong_class_routes_move`.
- `tests/hermes_cli/test_kanban_quota_episode_false_wall.py`: `test_hinted_episode_wall_holds_only_while_the_live_pool_confirms_it`, `test_a_disproved_short_hint_keeps_the_doubling_backoff`, `test_reclaim_phase_prunes_a_provider_wall_the_live_pool_disproves`.
- `tests/hermes_cli/test_kanban_quota_episode_live_release.py`: `test_hinted_episode_with_a_non_exhausted_pool_entry_is_admitted`, `test_hinted_episode_with_every_pool_entry_exhausted_still_holds`, `test_a_pool_with_no_usable_entry_still_holds`, `test_unknown_provider_episode_still_holds`, `test_quota_release_makes_the_card_admissible_and_shows_in_the_queue_note`, `test_drop_wall_keeps_a_wall_that_outlasts_the_released_hint`, `test_quota_release_refuses_worker_lineage`.
- `tests/hermes_cli/test_kanban_quota_episode_walls.py`: `test_class_alternate_ignores_require_model_named_in_prose`, `test_fresh_card_class_alternate_never_lands_on_another_wall`.

Coverage is limited to the entry points and fixture controls above; live services, deployment and user-interface behavior are not established by this documentation task.

## Applying

Use the [README full replay procedure](README.md#applying). Apply these patches in published ordinal order with the intervening series, replay bridges and mandatory post-replay repair described there. They are incremental changes, not independently certified cherry-picks. This document does not claim that the full mirror replay or feature suites ran during the documentation pass.

## Gotchas

These patches refine older documentation that said all strong/review routes wait unchanged. A chosen reviewer or explicit class-route pin is not interchangeable with an unpinned class allocation. Positive service evidence is required; empty/dead/unreadable pools do not prove recovery. Provider wall pruning is not broad cooldown deletion. Operator release is an audited episode boundary, not credential repair or a worker bypass. The CLI is the release entry introduced here; no equivalent tool/API release endpoint is claimed. Provider network calls and real account rotation are untested by this mirror pass.

## Plugin status

Core/CI implementation; no plugin is added or required. A plugin cannot replace the shared database, admission, worker or workflow invariants documented here.
