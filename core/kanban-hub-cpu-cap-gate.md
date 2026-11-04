# Kanban: hub CPU/cap final gate for worker-first claims

Patches: **1848** (`868bfc5269`, `feat(kanban): measured CPU busy sampler and root-only cpu_busy_below_pct reader [daveey]`), **1849** (`658b2be7cd`, `feat(kanban): hub reporter attaches measured CPU busy, 10 s cadence, 2 s hard-total no-replay POST [daveey]`), **1850** (`78e4ceeb03`, `feat(kanban): hub CPU/cap final gate with an independent full sample under admission.lock; origin claims skip worker load formula [daveey]`), **1851** (`18ef2e3351`, `test(kanban): keep relative-name board DBs out of the checkout (chdir tmp_path) [daveey]`), **1852** (`2e478ab239`, `chore(kanban): scripts/check health/footgun fixes for the hub CPU gate (utf-8-sig, kanban_db line cap, HX013 reasons) [daveey]`), **1853** (`51b6ad8064`, `test(kanban): hub CPU/cap gate across named boards, profiles, primary/local boards and legacy/host-bound/policy cards; a never-created default board no longer blinds the host cap [daveey]`), **1854** (`689616c674`, `fix(kanban): bounded CPU metrics POST shuts its socket on expiry so a trickling Board cannot keep the abandoned thread alive (HX012 trace) [daveey]`), **1855** (`72b3be808b`, `chore(kanban): rename the bounded call's wrapped connect so the websockets HX015 rule does not misfire on an http.client connect [daveey]`), **1856** (`ea49c9cf7b`, `fix(admission): chat/notification/review turns and prompt.admit outlast a hub claim's under-lock CPU sample instead of 5035 (waiting turns make claims defer; prompt.admit waits outside the resume lock) [daveey]`), **1857** (`8642b2e4b9`, `fix(kanban): hub gate refuses a full cap or passed deadline before its 1 s sample, recounting after it (review F6) [daveey]`), **1858** (`dfacd32253`, `fix(kanban): a Board grant the core never used is released with unused_grant: true so the Board keeps the card first-start (review F2 wire contract) [daveey]`), **1859** (`eeeef6aae0`, `docs/config(kanban): root cpu_busy_below_pct is a known off-by-default key; rollback keeps the root CPU guard and cap; advisory/sync-loop dependency and reporter budget pinned by tests (review F4, F7, F8) [daveey]`), **1860** (`6798d57d83`, `test(admission): prompt.admit busy-lock tests request the shared owner fixture by name (F811) [daveey]`), **1896** (`bd18747d7e`, `fix(kanban): enforce the hub claim's designed admission.lock hold; document cpu_busy_below_pct (docs drift)`), **1899** (`d8cdddffb4`, `[daveey] kanban: ruling 11 core half: a Board-granted rollback hub review claim is still refused by the root CPU gate (test); docs: post-rollback hub review behaviour and the all-host turn wait; fix stale BUSY_WAIT_S docstring`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- A measured CPU busy sampler and a root-only `kanban.admission.cpu_busy_below_pct` reader (off by default) (1848).
- The hub reporter attaches measured CPU busy on a 10 s cadence with a 2 s hard-total, no-replay POST (1849); the bounded POST shuts its socket on expiry (1854).
- Hub claims pass an advisory pre-check before the Board lease and a final gate under `admission.lock` with an independent 1 s sample, rechecking the whole-host cap and lease deadline; origin claims of their own policy cards skip the worker load formula (1850); a full cap or passed deadline is refused before sampling and recounted after (1857).
- Chat, notification and review turns and `prompt.admit` wait out a hub claim's under-lock sample instead of failing (1856).
- A Board grant the core never used is released with `unused_grant: true` so the card stays first-start (1858).
- The final gate refuses (`hub_cpu_unknown`) when no room is left for a nominal sample or the hold overran `CLAIM_MAX_HOLD_S`, and the key is documented (1896); a Board-granted rollback hub review claim is still refused by the root CPU gate (1899); config/docs (1859).
- Test and code-health follow-ups (1851–1853, 1855, 1860).

## How it works

`hermes_cli/machine_cpu_metrics.py` samples `/proc/stat`; `hermes_cli/kanban_admission.py` runs the final gate under `admission.lock`; `hermes_cli/kanban_sync_client.py` posts metrics; `hermes_cli/admission_fence.py`/`tui_gateway/methods_prompt_admission.py` let turns wait. Design rulings are in the fork's `docs/worker-first-v1.md`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1848: feat(kanban): measured CPU busy sampler and root-only cpu_busy_below_pct reader [daveey]
- 1849: feat(kanban): hub reporter attaches measured CPU busy, 10 s cadence, 2 s hard-total no-replay POST [daveey]
- 1850: - Advisory lock-free pre-check before the Board lease (ruling 5); final gate under admission.lock takes a real 1 s /proc/stat sample, rechecks whole-host cap and lease deadline (ruling 1 override). - Origin claiming its own policy card no longer uses the worker load1/CPUs formula (ruling 2). - Eight overflow queue reason codes; claimed event records the consumed sample. - No automatic structural origin repin: legacy origin reply still applied, new no_eligible_worker keeps fence (ruling 9). - Real-sample test asserts the whole sample ran with the lock held, hold <= 2.5 s, and the measured 0....
- 1851: test(kanban): keep relative-name board DBs out of the checkout (chdir tmp_path) [daveey]
- 1852: chore(kanban): scripts/check health/footgun fixes for the hub CPU gate (utf-8-sig, kanban_db line cap, HX013 reasons) [daveey]
- 1853: test(kanban): hub CPU/cap gate across named boards, profiles, primary/local boards and legacy/host-bound/policy cards; a never-created default board no longer blinds the host cap [daveey]
- 1854: fix(kanban): bounded CPU metrics POST shuts its socket on expiry so a trickling Board cannot keep the abandoned thread alive (HX012 trace) [daveey]
- 1855: chore(kanban): rename the bounded call's wrapped connect so the websockets HX015 rule does not misfire on an http.client connect [daveey]
- 1856: fix(admission): chat/notification/review turns and prompt.admit outlast a hub claim's under-lock CPU sample instead of 5035 (waiting turns make claims defer; prompt.admit waits outside the resume lock) [daveey]
- 1857: fix(kanban): hub gate refuses a full cap or passed deadline before its 1 s sample, recounting after it (review F6) [daveey]
- 1858: fix(kanban): a Board grant the core never used is released with unused_grant: true so the Board keeps the card first-start (review F2 wire contract) [daveey]
- 1859: docs/config(kanban): root cpu_busy_below_pct is a known off-by-default key; rollback keeps the root CPU guard and cap; advisory/sync-loop dependency and reporter budget pinned by tests (review F4, F7, F8) [daveey]
- 1860: test(admission): prompt.admit busy-lock tests request the shared owner fixture by name (F811) [daveey]
- 1896: The final hub gate now refuses (hub_cpu_unknown) when its cheap pre-sample checks leave no room for a nominal 1 s sample, or when sample + recount overran CLAIM_MAX_HOLD_S minus a 0.25 s commit reserve, so an admitted claim can no longer outlast a waiting turn's BUSY_WAIT_S (final integration review N1; the sample itself stays a full independent sample under the lock per the frozen contract). website configuration.md documents the root-only kanban.admission.cpu_busy_below_pct key, which the docs-drift ratchet required (red CI at 6798d57d834). [daveey]
- 1899: [daveey] kanban: ruling 11 core half: a Board-granted rollback hub review claim is still refused by the root CPU gate (test); docs: post-rollback hub review behaviour and the all-host turn wait; fix stale BUSY_WAIT_S docstring

## Code

`hermes_cli/machine_cpu_metrics.py`, `hermes_cli/kanban_sync_client.py`, `hermes_cli/machine_drain_metrics.py`, `hermes_cli/machine_metrics.py`, `docs/worker-first-v1.md`, `hermes_cli/kanban_admission.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_db_sync.py`, `hermes_cli/kanban_queue_reason.py`, `hermes_cli/admission_fence.py`, `hermes_cli/backend_retirement.py`, `tui_gateway/methods_prompt_admission.py`, `hermes_cli/config_defaults.py`, `website/docs/user-guide/configuration.md`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_admission_fence.py`
- `tests/hermes_cli/test_hub_cpu_gate_budget.py`: `test_slowest_reporter_round_refreshes_the_advisory_inside_its_freshness_window`, `test_advisory_is_recorded_before_the_send_so_a_board_outage_does_not_starve_local_claims`, `test_root_cpu_gate_key_is_known_documented_and_off_by_default`
- `tests/hermes_cli/test_kanban_hub_admission.py`: `test_gate_absent_admits_without_sampling`, `test_below_limit_admits_and_claimed_event_carries_consumed_sample`, `test_at_or_above_limit_or_unknown_refuses_with_no_local_run`, `test_invalid_root_limit_refuses_without_sampling`, `test_profile_override_cannot_disable_root_gate`, `test_sample_is_taken_while_admission_lock_is_held`, `test_advisory_precheck_defers_before_any_lease_or_sample`, `test_advisory_age_boundary_15000_ms_is_fresh`, `test_whole_host_cap_is_rechecked_under_lock`, `test_lease_expiring_during_sample_cannot_start`, `test_simultaneous_claimers_at_6_of_7_start_at_most_one`, `test_real_sample_is_entirely_under_lock_bounded_and_measures_chat_turn_cost` (+6 more)
- `tests/hermes_cli/test_kanban_hub_admission_scopes.py`: `test_root_gate_refuses_every_scope_under_lock_and_releases_only_its_grant`, `test_root_gate_admits_every_scope_below_limit_with_consumed_sample`, `test_profile_resolves_to_the_shared_root_not_its_own_home`, `test_whole_host_cap_counts_runs_from_every_profile_and_board`, `test_unsynced_local_board_does_not_blind_the_host_cap`, `test_grant_refused_by_the_final_gate_is_released_as_unused`, `test_reclaim_of_a_started_run_is_not_released_as_unused`, `test_rollback_hub_review_continuation_board_grant_is_still_cpu_gated_by_the_root`
- `tests/hermes_cli/test_kanban_hub_admission_turns.py`: `test_turn_wait_budget_outlasts_the_longest_designed_hold`, `test_turn_reserving_during_a_hub_claim_sample_is_admitted_after_the_hold`, `test_a_waiting_turn_makes_the_next_claim_defer_without_sampling`, `test_busy_reservation_registers_as_a_waiting_turn_until_admitted`, `test_crashed_waiter_marker_is_not_a_waiting_turn`, `test_update_hold_still_refuses_a_waiting_turn_at_once`
- `tests/hermes_cli/test_kanban_queue_integration.py`: `test_legacy_board_origin_return_records_reason_and_unfences_origin`, `test_no_eligible_worker_queues_and_keeps_fence`
- `tests/hermes_cli/test_kanban_sync_client.py`: `test_hub_lease_client_release_marks_an_unused_grant_only_when_asked`
- `tests/hermes_cli/test_kanban_sync_connected_feed.py`
- `tests/hermes_cli/test_kanban_sync_metrics_budget.py`: `test_cpu_report_has_hard_total_budget`, `test_cpu_report_succeeds_inside_budget_and_plain_report_keeps_normal_path`, `test_cpu_report_budget_is_two_seconds`, `test_abandoned_bounded_call_thread_ends_promptly_after_the_budget`, `test_connect_finishing_after_expiry_cuts_its_own_socket`
- `tests/hermes_cli/test_kanban_sync_targeted_rounds.py`
- `tests/hermes_cli/test_kanban_worker_policy_db.py`: `test_origin_claiming_its_own_card_skips_worker_load_formula`
- `tests/hermes_cli/test_machine_cpu_metrics.py`: `test_parse_sums_user_through_steal_and_never_adds_guest_twice`, `test_parse_malformed_is_unknown`, `test_busy_pct_definition_steal_counts_busy_iowait_idle`, `test_busy_pct_counter_reset_and_zero_total_are_unknown`, `test_sample_reports_complete_triple`, `test_sample_outside_1_to_2s_window_is_unknown`, `test_sample_window_boundaries_accepted`, `test_sample_io_error_and_non_linux_are_unknown`, `test_sample_on_this_host_is_real_and_bounded`, `test_below_is_strict_and_never_rounds`, `test_valid_limit`, `test_low_load_high_busy_rejects_and_high_load_low_busy_passes` (+2 more)
- `tests/hermes_cli/test_machine_metrics.py`: `test_reporter_sends_at_most_every_10s_and_backs_off_on_old_board`, `test_cpu_triple_only_when_root_gate_configured_and_sampled_last`, `test_incomplete_cpu_measurement_is_omitted_never_coerced_to_idle`, `test_old_board_fallback_strips_cpu_and_drain_fields_once`, `test_failed_cpu_report_is_never_replayed_next_round_takes_a_fresh_sample`, `test_worker_load_fields_unchanged_by_cpu_gate`, `test_active_card_runs_absent_default_board_counts_named_but_vanished_named_board_fails_closed`
- `tests/tui_gateway/test_prompt_admission_busy_lock.py`: `test_busy_update_lock_admits_once_after_the_hold_without_holding_the_resume_lock`, `test_lock_busy_past_the_budget_still_refuses_with_5035_and_nothing_admitted`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- A merge in the source branch (`04bc5b36944`) hand-resolved `hermes_cli/kanban_db.py` against the capsule stack; the replay carries that resolution as a bridge before 1896 (see the verification report).

## Plugin?

No: these change core modules that plugins cannot hook.
