# Durable notices through lease contention, reaping and update handoff

Patches: **2745, 2757, 2758, 2764, 2767, 2781, 2786**.

Related context: [headless-wake-lease-reap-2678.md](headless-wake-lease-reap-2678.md), [kanban-stale-block-ask-refresh-2678.md](kanban-stale-block-ask-refresh-2678.md).

## Behavior

A notice held after turn admission refuses keeps its session alive, unless the user pressed Stop. Completion notes wait for another turn's lease and recover previously failed, unnoted completion admissions. Concurrent remote-notice ledger creation retries a busy WAL switch. Headless cold resumes leave an update-paused turn to its restarting backend during the handoff window instead of continuing it in a process with no attached viewer.

## How it works

2745 moves timeout/quota-episode/repin/parent-unclaimed suffixes to `kanban_notice_text.py`, retaining imported aliases. 2758 similarly moves HUD/voice model-input note and prefix helpers into `session_notifications_surface.py`, registers them on the server, and preserves the deferred `tool_call` desktop-tool accounting. 2757 includes `_notif_held` in pending-notification ownership after the stopped-session check. 2764 pins wake-fold behavior: a last-card tail running on mid-line stays unchanged and DB lookup follows the notifier's environment pin. 2767 makes `_write_note` re-read the physical compression tip on each `SessionTurnLeaseLostError`, backing off 0.25–2 seconds and warning at most once per 60 seconds. The gateway startup watcher scans at most 200 newest failed terminal admissions; only local async completions missing both their admission-keyed note and text in the parent are reopened. Ledger claims still spend attempts; age/attempt caps terminally drop with a logged reason. Existing failed admissions receive the note exactly once, not a second inference. 2781 retries locked `journal_mode=WAL` within the same five-second budget. 2786 adds `paused_turn` deferral to the producer sweep and a five-second reread loop for re-adopted exits, which cannot discard their parked delivery. `successor_owes_continuation` uses 300 seconds from this process's birth if it started after the pause, otherwise 660 seconds from the pause freshness stamp. Past the window ordinary resume is allowed.

## Code

- `gateway/completion_admission_recovery.py`
- `gateway/run_notifications.py`
- `gateway/session_completion_notes.py`
- `tools/async_delegation.py`
- `tui_gateway/kanban_notice_text.py`
- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/methods_prompt.py`
- `tui_gateway/producer_admission.py`
- `tui_gateway/session_notifications.py`
- `tui_gateway/session_notifications_surface.py`
- `tui_gateway/update_pause.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_completion_admission_reconcile.py`: `test_failed_admission_is_reopened_and_noted_exactly_once`, `test_refused_recovery_note_spends_attempts_until_dropped`, `test_reopen_past_the_replay_age_drops_it_with_a_logged_reason`.
- `tests/gateway/test_completion_note_turn_lease.py`: `test_note_held_off_by_another_turns_lease_lands_once_it_releases`, `test_note_reclaims_a_dead_holders_lease`.
- `tests/gateway/test_kanban_wake_fold.py`: `test_refresh_claimed_leaves_a_row_whose_last_card_tail_runs_on_mid_line_alone`, `test_the_relabel_reads_the_notifiers_db_under_the_env_pin`.
- `tests/tui_gateway/test_cold_resume_yields_paused_turn.py`: `test_producer_sweep_leaves_a_fresh_paused_turn_to_its_successor`, `test_producer_sweep_wakes_a_pause_no_successor_picked_up`, `test_readopt_wake_waits_out_a_fresh_pause_then_resumes`, `test_readopt_wake_finds_the_chat_its_successor_loaded_here`, `test_a_waker_restarted_with_the_writer_counts_from_its_own_start`, `test_a_waker_that_did_not_restart_counts_from_the_pause_stamp`, `test_process_start_is_this_process_birth`.
- `tests/tui_gateway/test_detached_process_notify_keepalive.py`: `test_notice_held_for_refused_turn_keeps_session`, `test_notice_held_after_stop_does_not_pin_session`.
- `tests/tui_gateway/test_kanban_remote_observations_lock.py`: `test_a_busy_wal_switch_is_retried_not_raised`.
- `tests/tui_gateway/test_wake_yields_paused_turn.py`: `test_headless_wake_leaves_a_fresh_paused_turn_to_its_successor`, `test_wake_continues_a_pause_no_successor_picked_up`.

Coverage is limited to the entry points and fixture controls above; live services, deployment and user-interface behavior are not established by this documentation task.

## Applying

Use the [README full replay procedure](README.md#applying). Apply these patches in published ordinal order with the intervening series, replay bridges and mandatory post-replay repair described there. They are incremental changes, not independently certified cherry-picks. This document does not claim that the full mirror replay or feature suites ran during the documentation pass.

## Gotchas

The extracted suffix/surface helpers introduce no new user behavior and have no new dedicated tests in 2745/2758. Stop deliberately removes held-notice keepalive. A drain can cancel the completion-note lease wait; recovery remains bounded and does not promise infinite replay. WAL retry only handles locked errors. Handoff is a bounded heuristic, not proof that a successor will arrive; a live pause writer remains its own owner's concern. Tests cover helpers and backend entry points with fixtures, not attached Desktop clients or real upgrade rollouts.

## Plugin status

Core/CI implementation; no plugin is added or required. A plugin cannot replace the shared database, admission, worker or workflow invariants documented here.
