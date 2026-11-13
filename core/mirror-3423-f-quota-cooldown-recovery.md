# Model-scoped quota cooldown recovery and contradiction reporting

Patches: **3245–3250**.

## Behavior
- A credential pool's per-model cooldown ("bench") is cleared only by verified recovery of the observed model, using generation-aware clear receipts; other models' benches (including Codex) are preserved, and a new failure after a clear benches again.
- Recovery refuses a changed or unsafe row, a disabled account, an owned row without its secret, and a sanitized borrowed row whose identity changed; a sanitized borrowed row can recover by fingerprint without persisting the secret.
- Request-bucket evidence (retry-after, request reset) overrides conflicting generic reset fields; a captured generic 429 with genuine weekly headers keeps the weekly bench; non-finite, past, or missing evidence preserves the existing context; a rejected unified window wins over a short request bucket.
- An external model reset is seen by an existing pool; a stale pool writer's flush does not resurrect an externally cleared bench, and a legacy model bench loses to an external reset.
- Account swap does not inherit the old account's exhaustion; a same-account refresh keeps real exhaustion; an ordinary flush with an unchanged token pair adopts only model-scoped state.
- Owner side: contradictions (a probe proving a benched model works) are counted persistently across instances, de-duplicated into one outbox receipt per threshold, and drained by the owner into one card, then comments while it is open (a new card after it is done). A managed worker cannot drain.
- Completion proofs: the probe's completed response body decides the outcome (a bare 200 does not), a trickling response is cut at an absolute deadline, a body arriving after the deadline is not proof, probes run concurrently, and DNS resolution is bounded by a reaped resolver so a hung lookup cannot extend past the deadline.

## How it works
- 3245 adds model-state, recovery and request-bucket modules and a merge hook. 3246 wires recovery into auth, the managed worker and dispatch maintenance, plus owner-side contradiction recording and drain. 3247 validates proof bodies and bounds streaming. 3248 adds socket-level tests that prove identity and transport boundaries (including mutant checks). 3249 runs DNS in a reaped resolver bounded by the deadline. 3250 restricts `adopt_persisted_row` to model-scoped state unless the token pair moved, annotates the contradiction DB as local evidence, and widens test slack.

## Code
- `agent/credential_pool.py`
- `agent/credential_pool_model_cooldowns.py`
- `agent/credential_pool_model_state.py`
- `agent/credential_pool_recovery.py`
- `agent/request_bucket_context.py`
- `agent/agent_runtime_helpers.py`
- `agent/managed_worker.py`
- `hermes_cli/auth.py`
- `hermes_cli/auth_pool_merge.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_dispatch_maintenance.py`
- `hermes_cli/kanban_quota_detector.py`
- `hermes_cli/kanban_quota_probe.py`
- `hermes_cli/kanban_quota_recovery.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_model_cooldown_recovery.py`: `test_verified_recovery_clears_only_observed_model`, `test_new_failure_after_clear_still_benches`, `test_verified_recovery_refuses_changed_or_unsafe_row`, `test_codex_recovery_preserves_other_model_benches`, `test_verified_recovery_never_clears_unsafe_observed_generation`, `test_legacy_model_bench_loses_to_external_reset`, `test_new_account_failure_does_not_erase_model_reset_receipt`, `test_captured_generic_message_with_weekly_headers_keeps_weekly_bench`, `test_request_bucket_retry_after_not_usage_reset`, `test_external_model_reset_is_seen_by_existing_pool`, `test_stale_flush_does_not_resurrect_externally_cleared_model_bench`, `test_healthy_selection_does_not_read_model_state`, `test_account_swap_does_not_inherit_old_account_exhaustion`, `test_same_account_refresh_preserves_real_exhaustion`, `test_account_swap_adopts_all_disk_model_state`, `test_sanitized_borrowed_row_recovers_by_fingerprint_without_persisting_secret`, `test_sanitized_borrowed_row_refuses_changed_identity`, `test_nonfinite_request_retry_falls_back_to_request_reset`, `test_nonfinite_request_reset_preserves_context`, `test_header_only_request_retry_preserves_other_context`, `test_request_reset_fallback`, `test_invalid_or_past_request_reset_preserves_context`, `test_no_positive_request_bucket_evidence_preserves_context`, `test_non_429_preserves_context`, `test_missing_response_preserves_context`, `test_rejected_unified_window_wins_over_short_request_bucket`, `test_nonrejected_unified_window_does_not_mask_request_bucket`, `test_owned_row_without_secret_cannot_recover_by_borrowed_fingerprint`, `test_recovery_refuses_disabled_observation_when_current_label_is_enabled`.
- `tests/agent/test_quota_cooldown_contradictions.py`: `test_threshold_persists_across_instances_and_scopes`, `test_expired_events_do_not_count`, `test_concurrent_threshold_has_one_outbox_receipt`, `test_owner_delivery_failure_retains_pending`, `test_record_contradiction_stores_only_ids_and_cooldowns`, `test_owner_drain_creates_one_card_then_comments_when_open`, `test_drain_after_card_done_creates_new_card`, `test_managed_worker_cannot_drain`, `test_drain_failsoft_and_concurrent_owners_single_card`, `test_record_contradiction_failsoft`, `test_dispatch_tick_drains_via_owner_hook`.
- `tests/hermes_cli/test_kanban_quota_recovery.py`: `test_completion_uses_claude_code_wire`, `test_codex_requires_completed`, `test_recovery_cas_only_on_caller_and_success`, `test_late_completion_never_clears`, `test_startup_retries_once_without_double_probe`, `test_rejected_completion_never_retries`, `test_ineligible_rows_never_probe`, `test_default_contradiction_requires_persisted_recovery_and_skips_second_probe`, `test_completion_without_successful_cas_is_not_recovery`.
- `tests/hermes_cli/test_kanban_quota_detector.py` and `tests/hermes_cli/test_auth_pool_identity_transplant.py`: modified by 3246 (a pool-heal and disabled-account test pair among the names above may live here; exact per-file attribution not recorded).
- `tests/hermes_cli/test_kanban_quota_completion_mutations.py`: `test_real_socket_completed_body_decides_outcome`, `test_mutant_that_accepts_200_without_completed_body_is_killed`, `test_trickle_cut_at_absolute_deadline_and_peer_sees_close`, `test_mutant_without_absolute_deadline_overruns_and_is_killed`, `test_valid_body_arriving_after_deadline_is_not_proof`, `test_expired_deadline_makes_no_connection`, `test_recover_probes_concurrently_clears_only_proven_and_cuts_slow_peer`, `test_mutant_serial_probing_is_killed`, `test_recover_all_stalled_clears_nothing_within_deadline`, `test_hung_dns_does_not_extend_completion_past_deadline`.
- `tests/hermes_cli/test_kanban_quota_dns_deadline.py`: `test_dns_deadline_in_fresh_interpreter_and_mutants`, `test_resolver_roundtrips_addresses_without_network`, `test_resolver_failure_is_closed_and_reaped`, `test_resolver_explicit_reap_guard_and_mutant`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- 3245 and 3246 were landed as checkpoints with terse messages; 3250 then fixes two regressions they introduced (a flush re-benching a live re-authenticated credential).
- The test files list above merges names by feature; some names may sit in a neighboring file of the same patches.
