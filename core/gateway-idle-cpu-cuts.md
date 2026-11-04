# Gateway and Kanban idle CPU cuts

Patches: **1711** (`a10445e40c`, `fix(gateway): skip unused idle peer and owner scope work [daveey]`); **1718** (`1d19c6b382`, `[daveey] Reduce idle kanban watcher scans and connection churn`); **1738** (`61a5bc3017`, `[daveey] test: close seven watcher idle mutation coverage gaps`); **1739** (`ac613a6e3c`, `[daveey] test: freeze registry clock before concurrent fetches`); **1758** (`847887ec3c`, `[daveey] Gate idle heartbeat/loop scans and cache board row materialization`); **1759** (`4e36cefb6c`, `[daveey] board_rows: close evicted observers outside the module lock`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- The control socket and session-hosted service skip idle peer and owner-scope work that no caller uses (1711).
- Idle Kanban watchers scan less and churn fewer connections (1718); seven idle-mutation coverage gaps are closed by tests (1738/1739).
- Heartbeat restore and the loop watcher cache only proven-empty metadata, keyed on the reader connection's revision (`data_version` + `total_changes`); the dashboard caches materialized board rows (1758).
- `board_rows.evict()` unpublishes under its module lock and closes observers after releasing it, removing an ABBA deadlock with the per-board lifecycle lock (1759).

## How it works

Results that are active, corrupt or errors are never cached; only an empty proof under an unchanged revision is reused.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1711: (subject only)
- 1718: (subject only)
- 1738: (subject only)
- 1739: (subject only)
- 1758: Fifth idle-CPU cut, core half (kanban t_01a19d7fc2cfe36a, parent t_01a199a0a5d38255).
- 1759: Closing a board connection takes main's per-board lifecycle lock; evict() held board_rows._lock across that close while a locked connect() holds the lifecycle lock and calls evict() via recover(), an ABBA deadlock when the sync pool's recover() races it. Unpublish under _lock, close after.

## Code

`gateway/control_socket.py`, `gateway/kanban_watchers_notifier.py`, `gateway/run_goals.py`, `gateway/run_idle_gates.py`, `gateway/session_hosted_service.py`, `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_db_dispatch_guard_notice.py`, `hermes_cli/kanban_db_notify.py`, `hermes_cli/kanban_idle_candidates.py`, `hermes_cli/kanban_owner_archive_relay.py`, `hermes_cli/kanban_reader_retention.py`, `hermes_cli/kanban_worker_node.py`, `hermes_cli/loops.py`, `plugins/kanban/dashboard/board_prewarm.py`, `plugins/kanban/dashboard/board_read.py`, `plugins/kanban/dashboard/board_rows.py`, `plugins/kanban/dashboard/plugin_api.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/gateway/test_control_socket_idle_peer.py`: `test_private_connection_rechecks_home_and_peer`, `test_public_connection_never_looks_up_peer`
- `tests/gateway/test_kanban_notifier_idle_claims.py`: `test_collector_does_not_claim_idle_subscription`, `test_collector_retains_live_claims_for_expiry_recovery`
- `tests/gateway/test_run_idle_gates_empty_proofs.py`: `test_commit_is_seen_on_the_next_check`, `test_commit_landing_during_the_proving_scan_is_seen_next_check`, `test_heartbeat_and_loop_proofs_are_independent`, `test_read_errors_and_unparseable_rows_are_rechecked`, `test_root_loop_gate_does_not_warn_on_a_corrupt_row_every_tick`, `test_root_loop_scan_skips_list_active_loops_while_proven_empty`, `test_unchanged_store_is_scanned_once_across_k_checks`, `test_uncommitted_state_never_becomes_a_proof`, `test_writer_fallback_sees_its_own_commits`
- `tests/gateway/test_session_hosted_idle_scope.py`: `test_incomplete_preparation_remains_in_owner_scope`, `test_initialized_service_skips_scope_but_keeps_readiness_and_restart_gates`
- `tests/hermes_cli/test_kanban_watcher_idle_work.py`: `test_candidate_key_failure_falls_back_to_real_query`, `test_candidate_memo_bypassed_for_transactions_and_missing_triggers`, `test_candidate_memo_skips_scan_and_external_status_commit_invalidates`, `test_candidate_memo_survives_dispatch_connection_reopen`, `test_empty_blocker_sweeps_skip_and_new_candidates_run`, `test_empty_reclaim_skips_worker_sweeps_but_running_retries`, `test_no_guard_candidates_avoids_registry_then_new_hold_runs`, `test_no_running_tasks_still_closes_dangling_runs`, `test_notifier_scan_filters_idle_and_disconnected_subscriptions`, `test_notify_count_reuses_reader_and_sees_external_commit`, `test_notify_idle_reader_retention_is_bounded_and_missing_path_closes`, `test_owner_archive_candidate_scan_is_not_repeated` (+4 more)
- `tests/plugins/test_kanban_board_read_cache.py`
- `tests/plugins/test_kanban_board_rows_cache.py`: `test_board_payload_matches_a_cold_read`, `test_commit_during_the_row_read_is_read_again`, `test_config_and_profiles_on_disk_refresh_without_sql`, `test_continuously_polled_board_reopens_after_sidecar_loss`, `test_drop_waits_for_the_borrower_before_closing`, `test_evict_never_closes_under_module_lock_against_lifecycle_lock`, `test_external_process_commit_is_read_on_the_next_refresh`, `test_failed_read_is_retried_and_not_cached`, `test_heartbeat_only_write_is_read_on_the_next_refresh`, `test_idle_observer_is_closed`, `test_local_commit_is_read_on_the_next_refresh`, `test_observer_is_a_registered_guarded_handle` (+12 more)

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- Corrupt rows are still retried each sweep; only the per-sweep warning is quieted.

## Plugin?

No: these change core modules that plugins cannot hook.
