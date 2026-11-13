# Unloaded-notice sweeper and producer admission: host pass under a lease

Patches: **3471**.

## Behavior
- The host-wide unloaded-chat sweep (`kanban_unloaded`) and the producer-admission sweep no longer run in every gateway, Desktop serve and dashboard process. One process holds the `kanban-unloaded` lease (role preference gateway > serve > dashboard) and runs the host pass; the `producer-admission` sweep runs only in its own lease holder.
- Every other process runs a residual pass only for chats it holds itself (delegation and guarantee limited to live chats).
- The lease holder gets 8 wake slots per pass kind (`HERMES_KANBAN_HOLDER_WAKE_SLOTS`); standbys and the kill switch keep 2. With no free slot, the local sweep rewinds the claim and the remote sweep releases its ledger claim (`deferred:slots`) for retry, until the notice is overdue, when the passive row is written as before.
- Notice-fanout cursors and the remote snapshot persist in `notice-fanout.db` under the kanban home, so a lease hand-off resumes the interval (memory fallback if the file can't open).
- `HERMES_HOST_LOOP_LEASE=0` restores per-process passes.
- The source commit reports the old loops as 14.9% and 3.9% of steady serve samples; that is a source measurement, not tested here.

## How it works
3471 adds lease plumbing to `hermes_cli/host_loop_lease.py`, splits `kanban_unloaded._loop` into holder (host) and standby (residual) passes, adds `tui_gateway/notice_fanout_state.py` for persisted cursors, gates `producer_admission.sweep_once` on its lease, and threads `only=live` through `delegation_unloaded.sweep_once` and `notice_guarantee.guarantee_pass`. The holder also retries killed-subagent notices over every profile store.

## Code
- `hermes_cli/host_loop_lease.py`
- `tui_gateway/kanban_unloaded.py`
- `tui_gateway/notice_fanout.py`
- `tui_gateway/notice_fanout_state.py`
- `tui_gateway/notice_guarantee.py`
- `tui_gateway/delegation_unloaded.py`
- `tui_gateway/producer_admission.py`
- `tui_gateway/reap_notices.py`
- `tui_gateway/kanban_remote_late.py`
- `tui_gateway/kanban_remote_notices.py`
- `tests/tui_gateway/test_kanban_unloaded_host_lease.py`
- `tests/tui_gateway/test_kanban_unloaded_wake.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_kanban_unloaded_host_lease.py`: `test_the_holder_runs_the_host_pass_and_beats`, `test_a_standby_runs_only_the_residual_pass_for_its_live_chats`, `test_a_standby_with_no_live_chat_does_nothing`, `test_kill_switch_runs_the_host_pass_in_every_process`, `test_the_loop_releases_its_lease_when_stopped`, `test_an_unloaded_chat_is_delivered_exactly_once_by_the_holder`, `test_a_live_chat_in_a_standby_gets_its_delegation_from_its_own_residual_pass`, `test_a_live_chat_in_a_standby_gets_its_closeout_from_its_own_residual_pass`, `test_the_host_pass_retries_killed_notices_in_every_profile_store`, `test_the_holder_gets_eight_wake_slots_and_a_standby_its_base`, `test_slot_exhaustion_rewinds_the_claim_and_retries_instead_of_a_passive_row`, `test_a_remote_notice_with_no_free_slot_is_handed_back_until_overdue`, `test_a_lease_hand_off_keeps_the_card_change_interval`, `test_a_lease_hand_off_keeps_the_remote_snapshot`, `test_producer_admission_sweeps_only_in_the_lease_holder`, `test_process_role`, `test_an_overdue_notice_with_no_free_wake_slot_writes_the_row_now`.
- `tests/tui_gateway/test_kanban_unloaded_wake.py`: small adjustment only.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A standby's live chats depend on its own residual pass; chats held by another process return `skipped:held`.
- The per-chat wake budget is now counted by the holder, so it is effective host-wide.
