# Kanban wake-probe placement, ended-turn notice re-offer, ask re-check scope, origin-ledger evidence

Patches: 1205 1207 1212 1232

Four independent fixes in the Kanban layer: a woken remote notice whose turn ends without its row is re-offered; a parked card's `wake_check` probe survives worker-node eviction and is restored when lost; the standing-ask re-check reads only the ask's premise for card ids; the origin-ledger evidence comparison strips both sides.

## Behavior

- 1205: a remote Kanban notice that woke a chat turn is re-offered once if that turn ended (`running` false) and its `kanban_notice` row is still not in the transcript, instead of staying in flight until the backend exits. A still-running turn is never re-offered or re-woken (no wall-clock timeout).
- 1207: the standing-ask re-check (`named_cards_closed`) extracts task ids from the title, `action` and `why_human` only. A done card cited in `evidence` as proof no longer voids the ask.
- 1212: a park's `wake_check` runs on the gateway that parked the card (`probe_host` on the park event). A worker node keeps its scheduled copy of another gateway's card while its probe row is live. A lost probe is restored once; a second loss wakes the card with a comment. A relayed park whose probe host stops reporting is woken with a comment.
- 1232: the origin ledger accepts a completed-event preview as evidence when the 400-char cut lands right after a space (trailing whitespace on the cut line), so the owner-done card is released.

## How it works

- 1205: `_settle_started` looks up the woken chat's live session dict (`_live_woken_session`, by `sid`, then the lineage tip) and records it by reference in `_woken[(session, ref, state)]` (`_track_woken`). In `_claim_notice`'s holder-is-me branch, `ended = _woken_turn_ended(...)` is read BEFORE the transcript check, so a row written before the turn ended is acked. Otherwise, if ended and the key is absent, `_forget_woken` (single-winner pop) lets one caller return `"offer"`; `collect()` or the next sweep pass redelivers. `ack()` and `_reset_for_tests` also clear the entry. Tracking is in-process; a dead holder still takes the dead-holder path. The keyed dedupe in `record_kanban_notice` covers duplicate writes.
- 1207: `_named_task_ids` drops `evidence` from the scanned text.
- 1212: `park_fields(..., conn=, probe_host=)` adds `probe_host` (default `own_gateway(conn)`, read from `kanban_sync_state`) to the park payload when a `wake_check` is set. `park()` takes `probe_host` and calls `set_check` only when `probe_here(conn, probe_host)`. A capability relay to a home gateway passes `probe_host=gateway` (mode `RELAY_HOME`), so the worker registers no probe and the home registers it. `worker_node.evict` skips `scheduled` copies that have a `task_wake_checks` row. `wake_due_continuations` skips `foreign_ids(conn)` (worker copies; the home owns the clock wake). `restore_missing_checks` runs from `run_due_wake_checks` each dispatcher tick: for `scheduled` cards with no probe row and a park carrying `wake_check` + `wake_at`, it (a) re-registers locally and appends `wake_check_restored` once per park, then wakes with `wake_check_lost` on a second loss; or (b) for a park whose `probe_host` is another gateway on a card this gateway owns, wakes it when no `wake_check_alive` event arrived for `ALIVE_EVERY` (3 h) + 2x `check_every` + `SILENT_GRACE` (15 min). `_note_alive` (called from `apply_result`) emits `wake_check_alive` at most every `ALIVE_EVERY` on a worker's foreign copy. `_RESTORE_SEEN` dedupes per park event.
- 1232: `observe_owner_task` compares `recorded` against `_first_line(...).strip()` of both the raw and `public_summary` forms.

## Code

- 1205: `tui_gateway/kanban_remote_notices.py` (`_woken`, `_track_woken`, `_woken_turn_ended`, `_forget_woken`, `_live_woken_session`, `_claim_notice`, `ack`, `_settle_started`).
- 1207: `hermes_cli/kanban_block_policy.py` (`_named_task_ids`).
- 1212: `hermes_cli/kanban_wake_check.py` (`park_fields`, `own_gateway`, `probe_here`, `foreign_ids`, `foreign_copy`, `_note_alive`, `_lost_wake`, `restore_missing_checks`, `run_due_wake_checks`, `apply_result`), `kanban_timed_park.py` (`park`), `kanban_capability_routing.py` (`_park_relay`), `kanban_continuation.py` (`continue_task`, `wake_due_continuations`), `kanban_db_schedule.py` (`schedule_task`), `kanban_worker_node.py` (`evict`).
- 1232: `hermes_cli/kanban_origin_ledger.py` (`observe_owner_task`).

## Tests

- 1205: `tests/tui_gateway/test_kanban_remote_wake.py`: `test_woken_turn_that_ended_without_its_row_is_reoffered_to_the_live_poller`, `..._is_rewoken_by_the_next_sweep`, `test_woken_turn_still_running_without_its_row_is_not_reoffered`.
- 1207: `tests/hermes_cli/test_kanban_ask_recheck.py`: new `test_card_cited_only_in_evidence_is_not_the_premise`; `test_all_named_cards_closed_moves_card_to_triage` now names the second card in `why_human`.
- 1212: new `tests/hermes_cli/test_kanban_wake_check_home.py`: `test_a_relayed_park_keeps_one_live_probe_and_its_wake_reaches_the_home_board`, `test_a_relayed_probe_reports_alive_to_the_home_board`, `test_the_home_board_wakes_a_relayed_park_whose_probe_host_went_silent`, `test_a_lost_probe_is_restored_once_then_a_second_loss_wakes_the_card`, `test_the_dispatcher_tick_restores_a_lost_probe_and_runs_it`. `test_kanban_capability_relay.py` asserts `probe_host` is the home and `restore_missing_checks` registers the probe.
- 1232: `tests/hermes_cli/test_kanban_origin_legacy_release.py::test_receipt_binds_the_full_handoff_not_the_events_first_line`, three new parametrized cases (cut after a space; same through the FINAL SEND preview; multi-line handoff).
- Untested: the 1205 dead-holder path with a real process death (unchanged, covered earlier); the `copy and not host` legacy-park skip and the `wake_due_continuations` foreign-copy skip in 1212 have no dedicated test.

## Applying

Apply the whole numbered series at the merge-base with `git am -3 --empty=keep`; patches are not independent. 1205 builds on the earlier woken-notice ack change (`_settle_started` leaving the claim in flight; see kanban-goal-loop-sweeps-and-remote-notices.md). 1207 builds on the earlier standing-ask re-check (`kanban_block_policy` sweep + `test_kanban_ask_recheck.py`). 1212 builds on the capability relay and worker-node code (`_park_relay`, `evict`). 1232 builds on the origin-ledger legacy-release change. Among these four, no patch depends on another. No config required.

## Gotchas

- 1205: the `running` flag must be read before the transcript check, or a row written just before the turn ends would be re-offered as a duplicate. If the woken chat is not live in this process, nothing is tracked and the claim waits for its row or the backend's exit.
- 1212: `task_wake_checks` is machine-local and never syncs; that is why probe placement and the alive heartbeat exist. The silence threshold is over 3 h, so a dead probe host is noticed late by design. Only the home gateway wakes a silent relayed probe. Clock wakes of a worker copy are left to the home board.
- 1212: `restore_missing_checks` skips a card when a `claimed`/`spawned` event is newer than the park (the next park has not synced yet).
- 1207: a card that is genuinely the premise but written only in `evidence` is no longer detected as closed.
- 1232: only whitespace at the ends is normalized; any other mismatch still fails `evidence_ok`.
