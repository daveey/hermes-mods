# Process completion custody across gateway restarts

Patches: **2932, 2933, 2948, 2978**.

## Behavior
- A notify-on-complete background process that exits between its result receipt and the watcher tick is still admitted exactly once after a gateway restart.
- Replay after a lost acknowledgement resolves to the existing admission; refusals and repeated restarts keep custody until admitted.
- Reading the output (wait/log) or an explicit kill settles the owed notice, so it is not re-announced; a bulk cleanup kill keeps an existing obligation but never creates one.
- Malformed owed receipts are bounded and skipped without stopping other recovery; an over-cap retained receipt stays retrievable.
- A failed delegation admission that a Desktop client already delivered is not reopened at the next start.

## How it works
- 2932 writes a `delivery_obligation` block with the result receipt (same atomic write, before checkpoint removal) for gateway-routed jobs; only a messaging gateway re-arms owed completions.
- 2933 makes the kill-path rewrite and the watcher acknowledgement run under the registry lock (`settle_completion`).
- 2948 bounds malformed receipts, charges failed reconstruction to the start budget, and logs kill-rewrite settlement.
- 2978 makes completion-admission recovery treat a delegation as delivered when the target session already holds the Desktop-stamped row.


## Code
- `gateway/completion_admission_recovery.py`
- `gateway/run_process_watcher.py`
- `hermes_state_common.py`
- `tools/async_delegation.py`
- `tools/process_registry.py`
- `tools/process_registry_checkpoint.py`
- `tools/process_registry_obligations.py`
- `tools/process_registry_results.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_completion_admission_desktop_delivery.py`: `test_coalesced_admission_reopens_only_undelivered_delegation`, `test_desktop_delivered_failed_admission_is_not_reopened`, `test_reopened_admission_delivered_by_batch_is_reopened_once`.
- `tests/gateway/test_process_completion_custody.py`: `test_kill_that_races_the_reader_exit_settles_only_an_explicit_kill`, `test_lost_acknowledgement_replay_resolves_to_the_existing_admission`, `test_no_notify_and_other_profile_jobs_do_not_wake`, `test_only_a_messaging_gateway_rearms_owed_completions`, `test_owed_receipts_survive_retention_and_a_malformed_one_skips_alone`, `test_refusal_and_repeated_restarts_keep_custody_until_admitted`, `test_reopen_marks_one_exact_receipt_and_refuses_an_admitted_one`, `test_restart_between_exit_and_watcher_tick_admits_the_exact_completion_once`, `test_wait_or_log_consumption_suppresses_the_notice_across_restart`.
- `tests/tools/test_process_completion_receipt_nits.py`: `test_bad_budget_preserves_non_owned_custody`, `test_explicit_kill_rewrite_logs_settlement`, `test_failed_reconstruction_spends_start_budget`, `test_malformed_receipt_is_bounded_without_stopping_other_recovery`, `test_retained_over_cap_owed_receipt_is_retrievable`, `test_settlement_and_kill_rewrite_serialize`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
2932 records that the exact kill interleaving that lost the original notice was unproven. The Desktop-delivery check in 2978 relies on transcript metadata; other delivery paths are untested.
