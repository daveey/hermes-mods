# Hub admission: CPU gate census and claim screen

Patches: **3121, 3122, 3123, 3151, 3152, 3158, 3159, 3160, 3162**.

## Behavior
- The final hub gate no longer refuses claims as `hub_cpu_unknown` merely because the whole-host board census is slow under thread contention.
- The locked final gate reads the sampler's fresh CPU sample instead of measuring again under the admission lock; refusals name their stage and hold/budget evidence.
- The hub claim screen defers a card edited while it was being judged, caps judge calls per tick, and judges in dispatch order so priority is never inverted.
- A deny move records a real `previous_runs_on`, and the unjudged-claims gate counts verdicts carried in the created payload.

## How it works
- 3121 and 3123 add RED contention tests and re-export the board fixture without shadowing.
- 3122 reads each board's running rows, own-holder filter and pool receipts in one statement (legacy schemas keep the old path), connects via `absolute()`, and logs the refusing stage with `hold_ms`/`budget_ms`; CPU limit, window and cap unchanged.
- 3158 makes the locked gate consume the sampler file's sample, keeping lease headroom so the accepted sample stays fresh; 3159 renames a slow-window test stand-in; 3160 reconciles stages: `limit_unknown`, `lease_expired_before_census`, `sample_stale_or_missing`, `lease_expired_after_census`, and an overrun refuses as `hub_admission_overrun` with stage `hold_after_census`.
- 3151 records `Counts.deferred_ids` for edited-while-judged cards (left out of that tick's ready lane) and caps judge calls per tick; 3152 walks candidates in `lane_rows` dispatch order; 3162 fixes `previous_runs_on` on deny moves and the unjudged-claims gate.

## Code
- `hermes_cli/kanban_admission.py`, `hermes_cli/kanban_admission_pools.py`
- `hermes_cli/machine_metrics.py`, `hermes_cli/machine_cpu_metrics.py`, `hermes_cli/kanban_queue_reason.py`
- `hermes_cli/kanban_hub_admission_claim.py`, `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_db_sync.py`
- `docs/worker-first-v1.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_hub_cpu_gate_contention.py`: `test_contended_census_leaves_the_sample_room_inside_the_hold`, `test_census_counts_are_unchanged_by_the_cheaper_reads`, `test_an_overrun_names_its_stage_and_hold_in_the_refusal`, `test_each_unknown_branch_names_its_stage`.
- `tests/hermes_cli/test_kanban_hub_admission.py`: `test_one_read_census_matches_the_long_way`, `test_advisory_age_boundary_10000_ms_is_fresh_and_the_locked_read_keeps_15000`, `test_real_claim_reads_the_sampler_file_under_the_lock_and_holds_it_briefly`, `test_slow_census_overrun_refuses_as_overrun`, `test_pool_change_during_the_lease_checks_cpu_against_the_locked_census_pool`.
- `tests/hermes_cli/test_kanban_hub_admission_sampler_read.py`: `test_final_gate_admits_on_the_sampler_sample_without_measuring_under_the_lock`, `test_slow_locked_census_on_a_gil_starved_hub_still_admits_inside_the_budget`, `test_advisory_keeps_lease_headroom_so_the_accepted_sample_is_still_fresh_under_the_lock`, `test_sample_accepted_before_the_lease_is_still_fresh_after_a_slow_lease`, `test_sample_that_changes_after_the_advisory_is_rechecked_under_the_lock`.
- `tests/hermes_cli/test_kanban_hub_admission_claim.py`: `test_a_card_edited_while_judged_is_not_claimed_this_tick_and_is_judged_next_tick`, `test_judge_calls_are_capped_per_tick_and_the_rest_wait_unclaimed`, `test_screen_reports_deferred_ids`, `test_the_judge_budget_follows_dispatch_priority_not_creation_order`, `test_unjudged_claims_gate_opens_on_a_creation_payload_verdict`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The old per-board census made dozens of GIL handoffs and could exceed the 2.25 s hold, refusing every hub claim; the one-statement census exists to fit that hold.
