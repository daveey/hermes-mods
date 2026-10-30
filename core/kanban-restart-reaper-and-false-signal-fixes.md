# Kanban restart claims, reaper fingerprints and partial-board false signals

Patches: 1254 1265 1266 1269 1272

- `patches/1254-fix-kanban-reaper-treats-a-non-finite-fingerprint-as.patch`
- `patches/1265-fix-kanban-a-deferred-gateway-restart-claims-no-new-.patch`
- `patches/1266-feat-kanban-declare-GATEWAY_RESTART_SAFE-now-that-26.patch`
- `patches/1269-fix-gateway-normalize-non-finite-fingerprint-errors-.patch`
- `patches/1272-fix-kanban-a-Board-synced-host-with-no-hub-replica-c.patch`

## Behavior

- 1254: the terminal-worker reaper treats an infinite recorded process fingerprint as foreign evidence, clears its worker PID and does not signal the live stranger.
- 1265: a dispatcher with a deferred gateway restart requests no new claims, while wake probes and dependency promotion continue.
- 1266: `GATEWAY_RESTART_SAFE = True` declares the worker restart contract. Worker-entry-point tests exercise a draining owner followed by an unknown turn: the worker exits with the restart-loss code and the sweep requeues it without counting a failure.
- 1269: the gateway fingerprint comparator exposes `TypeError` or `ValueError` for junk on either side, including non-finite numbers, rather than leaking `OverflowError`.
- 1272: a Board-synced worker with no replica does not flag an absent card reference as phantom. A standalone board with sync disabled still flags an invented reference.

## How it works

1254 catches `OverflowError` in `_pid_recycled`; its stale-fingerprint fixture changes the start-time component of the string rather than performing SQLite arithmetic on the whole TEXT value. 1269 normalizes overflow to `ValueError` in `start_time_fingerprints_match` and extracts the unchanged argv renderer to `gateway/status_command_line.py`.

1265 maps `_restart_requested` to the existing `drain` dispatch gate only when no stronger gate is present. 1266 adds the literal flag read by the updater's source-text regex; the worker's existing refusal budget, restart-loss exit and reclaim logic implement the contract.

1272 makes `replica_task_resolution` non-authoritative when IDs were requested, no replica path exists and Board sync is enabled. Empty ID lists remain authoritative. The diff notes that unreadable sync configuration is interpreted as standalone; that error path is untested here.

## Code

`hermes_cli/kanban_db_dispatch.py`, `gateway/kanban_watchers.py`, `hermes_cli/kanban_worker_client.py`, `gateway/status.py`, `gateway/status_command_line.py`, and `hermes_cli/kanban_session_activity.py`.

## Tests

Tests included in the patches; not re-run for this documentation update:

- 1254: `tests/hermes_cli/test_kanban_terminal_worker_reaper.py::test_unrepresentable_numeric_fingerprint_is_foreign_not_a_crash` uses two SQLite expressions producing infinity; checks no signal, live child and cleared PID. The recycled/legacy-row fixture is also corrected.
- 1265: `tests/agent/test_core_update_drain.py::test_a_deferred_restart_claims_nothing_but_keeps_wake_probes_and_promotion` runs a dispatcher tick and checks no spawn plus wake/promotion to ready.
- 1266: `tests/hermes_cli/test_kanban_worker_owner_restart.py::test_a_worker_mid_turn_at_an_owner_restart_is_requeued_uncounted_end_to_end` invokes real worker `main` against a fake owner/clock and books the exit through both reaped status and log trailer. It checks ready, zero failures, `reclaimed`, one `owner_restart_lost` event and no crash/protocol streak. `test_the_client_declares_gateway_restart_safe_as_hermes_sync_reads_it` pins both flag and regex. A live service-manager restart and updater integration are untested by these new tests.
- 1269: `tests/gateway/test_owner_liveness_drift.py::test_junk_fingerprint_raises_contract_error` crosses both sides with positive/negative infinity, NaN, None and a nonnumeric string. The argv extraction has no new test in this patch.
- 1272: `tests/hermes_cli/test_kanban_false_signals.py::test_worker_node_summary_naming_unclaimed_home_board_card_is_not_flagged` and `test_standalone_host_without_hub_still_flags_made_up_id` check both events and computed diagnostics.

## Applying

Apply the complete numbered series at the documented merge-base with `git am -3 --empty=keep`; see `README.md`. These follow existing worker restart, reaper and replica machinery, rather than standing alone.

## Gotchas

- The restart-safe declaration does not implement restart recovery; keep the earlier worker changes with it.
- Restart recovery tests use a simulated owner, not a deployed gateway.
- No replica is not proof of absence on a partial synced board. This patch does not disable standalone phantom detection.
