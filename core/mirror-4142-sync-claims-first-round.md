# Sync claims wait for the first feed round

Mirror snapshot 4142; ordinals 4061, 4062, 4063.

## Behavior

- On a synced board in `primary` mode, a process that runs (or is about to run) the host's sync loop claims no task until one of its own sync rounds on that board has completed with the replica and live pulls at the feed head. The claim is deferred with reason `sync_first_round`, and one INFO log line is written per board while it is held.
- The gate fails closed: before the loop's first round, during it, and after a round that ended behind the head. Once a round reaches the head, claims stay open for that lock tenure; a later catch-up does not close them again.
- Boards in shadow or off mode, and processes with no sync loop (for example a claim from a shell), are not gated by their own state. Such a process does honour another live process's recorded gate, so a profile gateway holding the dispatcher lock waits while the root gateway holds the sync lock.
- `hermes kanban sync status` gains a `claims` field: `held (first round not at head)`, `open`, `not gated (mode ...)` or `no live sync loop recorded`.
- The outbox-age alarm no longer counts run rows that the push-before-pull hold keeps on purpose while a round is in progress or before the process's first round completes.
- 4063 separates a gateway watcher that is only waiting for the sync lock from the process that owns it.

## How it works

- 4061: new module `hermes_cli/kanban_sync_claims.py` keeps per-process state (loop flag, boards opened, boards already logged) and a small record file `kanban/.sync-claims.json` beside the sync lock holding pid and opened boards, written atomically. `kanban_db_sync.claim_gate` calls `held(conn)` for primary boards before the lease request and defers the claim if it returns true. The gateway sync watcher calls `loop_expected()` before waiting for the lock; `kanban_sync_stop.run_loop` calls `loop_started()`. `kanban_sync_client._sync_board_locked` now calls `backlog.round_started` at round start and `backlog.completed` at the end, which records the round and, when it reached the head, opens claims.
- 4061: in `kanban_sync_backlog`, the single "behind" set is split into `_DUE` (last completed round stopped at the page budget, rerun at once) and `_AT_HEAD` (last completed round reached the head). `behind(path)` is now true until a completed round of this process reached the head, so the outbox alarm ages only the sendable ops in that state. `round_started` only clears the rerun-at-once flag.
- 4062: the board-delivery test fixture resets the claim state so a previous test's loop cannot gate the next one. The patch 4061 also adds equivalent autouse resets to the gateway and CLI test conftests.
- 4063: state gains an `expected` flag (watcher may be waiting) separate from `loop` (this process holds the sync lock). `loop_started` clears opened boards, and a new `loop_stopped`, called in `run_loop`'s `finally`, clears them and removes this process's record file. A waiting watcher uses the current lock owner's live record; with no live record it stays held. `describe` follows the same rules.

## Code

Source paths touched by this feature group (including regression fixtures):

- `gateway/kanban_watchers.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_sync_backlog.py`
- `hermes_cli/kanban_sync_claims.py`
- `hermes_cli/kanban_sync_cli.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_outbox_age.py`
- `hermes_cli/kanban_sync_stop.py`
- `tests/gateway/conftest.py`
- `tests/hermes_cli/conftest.py`
- `tests/hermes_cli/test_kanban_sync_claim_gate.py`
- `tests/tools/test_kanban_board_delivery.py`

Mail entries in the ordered series:

- 4061: [`3a10a27f63412283fff6190eb7b8e91e88414004`](patches/4061-daveey-kanban-sync-claims-wait-for-this-process-s-fi.patch)
- 4062: [`075342c8a3d4c3021cd85649e05edf962ab7da7e`](patches/4062-test-kanban-isolate-sync-claim-state-in-delivery-fix.patch)
- 4063: [`9435baf78d09a2c99aa55774730ea14fa456b182`](patches/4063-fix-kanban-distinguish-waiting-sync-watchers-from-lo.patch)

## Tests

`tests/hermes_cli/test_kanban_sync_claim_gate.py` (new in 4061, extended in 4063), using the in-memory fake Board:

- `test_a_fresh_host_claims_nothing_during_its_snapshot_rebuild_round_and_claims_once_at_the_head`: a dispatcher tick in the middle of a multi-page snapshot rebuild leaves the card ready with no run or lease and reports the `sync_first_round` deferral; one hold log line; the card is claimed after the round.
- `test_a_round_that_ends_behind_the_head_keeps_claims_held`: claims stay held until a round reaches the head.
- `test_a_process_without_a_sync_loop_is_not_gated`, `test_a_shadow_board_is_not_gated`: direct claims still succeed.
- `test_another_live_process_loop_record_holds_and_opens_claims`: another live process's record holds, then opens, claims; a dead process's record is ignored.
- `test_sync_status_shows_the_gate`: the `claims` field is `held ...` then `open` (calls the status handler directly; the CLI entry is otherwise untested).
- `test_before_any_round_reaches_the_head_the_alarm_does_not_age_a_held_run_row` and `test_during_a_round_after_one_that_ended_behind_the_alarm_does_not_age_a_held_run_row`: the alarm age stays below the stale threshold; a completed round that ended behind still reruns at once.
- `test_a_failed_round_neither_reruns_at_once_nor_clears_behind`: `round_started` clears only the rerun flag.
- 4063: `test_waiting_sync_gateway_uses_current_lock_owners_record` (parametrized on a previous tenure) and `test_ended_sync_tenure_discards_its_readiness` (parametrized on normal return and crash): a waiting watcher defers to the owner's record; an ended tenure removes its record and closes its readiness.

`tests/tools/test_kanban_board_delivery.py` (4062): the `home` fixture resets claim state; no new test function.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- The gate delays claims on a restarted or new host until its first round finishes; the patch text reports roughly 35 seconds for a hub's first round after a restart, but this is a reported figure, not something this document verified.
- The FIFO hold of run rows before the head is unchanged; the change only stops the alarm from counting those rows and stops claims that would produce them.
- Gating applies only to `primary` boards. The record file is per host and holds one loop's state.
- Tests run with the gate reset by fixtures; production processes keep the module-level state.
- This document makes no deployment claim.

## Plugin?

No. The gate sits in the core claim path and the sync round bookkeeping, which a plugin cannot intercept.
