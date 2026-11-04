# Model routing state: WAL, and resume on the allocation store's route

Patches: **1774** (`108897f28e`, `[daveey] fix(routing): WAL routing state; sticky reuse prepares outside the writer lock`); **1792** (`3d9916b2dc`, `fix(routing): resume on the allocation store's route, not a lagging model_config copy [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- The routing state database uses WAL and sticky reuse prepares outside the writer lock, so allocations no longer time out with `database is locked` under contention (1774).
- Resume uses the allocation store's route, not a lagging `model_config` copy, so a desynced row no longer fails every build with `Exact pinned route cannot fail over` (1792).

## How it works

`agent/model_routing_state.py`, `agent/model_routing_restore.py`, class-routing call sites in gateway, API server, CLI and TUI.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1774: Problem: 2026-11-02 21:23-21:30 UTC on hermes-hub every model-routing allocation failed with "database is locked": managed workers at model_routing_state.py:32 (COMMIT) and :20 (CREATE TABLE IF NOT EXISTS), gateway context compression, delegate_task provider resolution and tui agent builds (managed-worker.log, agent.log, errors.log). Worker run 28218 died at startup.
- 1792: MacBook chat 20261026_205117_f67f0d sat on an unanswered kanban notice (unresolved{notice_tail}) for 7 h: every agent build for it (notice close-out wake, kanban sweep wake, chat-closeout, automation replay) failed with "Exact pinned route cannot fail over".

## Code

`agent/model_routing_restore.py`, `agent/model_routing_state.py`, `gateway/platforms/api_server_class_routing.py`, `gateway/run_class_routing.py`, `hermes_cli/cli_model_routing.py`, `tui_gateway/agent_factory.py`, `tui_gateway/class_routing.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/agent/test_model_routing_row_route_desync.py`: `test_allocation_store_wins_over_a_lagging_model_config_copy`, `test_no_allocation_row_falls_back_to_the_copy`, `test_repro_stale_copy_after_honoured_switch_is_refused`, `test_store_pin_policy_no_longer_approves_is_not_restored`, `test_store_wins_even_when_the_row_still_names_the_old_model`
- `tests/agent/test_model_routing_state_contention.py`: `test_busy_deadline_exhaustion_publishes_nothing_and_never_prepares`, `test_concurrent_process_allocators_under_a_long_reader_stay_unique_and_balanced`, `test_concurrent_same_session_allocators_share_one_decision`, `test_conversion_blocked_by_a_reader_does_not_fail_allocation`, `test_legacy_rollback_journal_file_converts_and_keeps_rows`, `test_long_reader_does_not_block_a_new_allocation_commit`, `test_peek_schema_and_sticky_reads_survive_a_reader_plus_waiting_writer`, `test_read_only_peek_on_wal_with_and_without_sidecars`, `test_rejected_first_allocation_leaves_schema_for_peek_and_link`, `test_slow_prepare_holds_only_first_allocations_not_sticky_reuse_or_peek`, `test_sticky_snapshot_never_advances_counters_and_writes_still_go_through_the_lock`
- `tests/agent/test_resume_allocation_store_wins.py`: `test_api_server_resume_restores_the_store_route`, `test_cli_resume_restores_the_store_route`, `test_desktop_resume_restores_the_store_route`, `test_gateway_restart_restores_the_store_route`, `test_restored_route_builds_without_refusal`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
