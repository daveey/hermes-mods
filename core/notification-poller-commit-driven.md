# TUI notification poller: commit-driven idle waits

Patch **2036** (`438f8a49be0b55551b62e5a32bb55e3e79013306`, `fix(tui): make idle notification polling commit-driven [daveey]`). Related idle memos: [idle-commit-keyed-scan-memos.md](idle-commit-keyed-scan-memos.md).

## Behavior

An idle TUI/Desktop session's notification poller (`tui-notif-poller-<sid>`) previously woke every 0.5 s and re-ran its source reads each time. Now:

- **Due-time waits.** With nothing queued or parked, the poller blocks until the next loop/Kanban (and, if anything is parked, parked-event) deadline instead of 0.5 s. A queue put still wakes it at once. Stops, held-event handoffs and parked-event publication wake it through a shared wake generation. A session holding work (`_notif_held`) keeps the old ≤0.5 s cap so busy-to-idle latency is unchanged.
- **Commit-keyed empty proofs.** These reads are skipped while the relevant SQLite file has had no commit since a proven-empty read:
  - async-delegation orphan sweep: the ledger has no `running`/`finalizing` or `pending` rows;
  - producer admission: no pending producer rows for this session's owner;
  - heartbeat/loop schedule managers: the schedule metadata row is absent;
  - Kanban board poll: no `tui` subscription for this chat is in flight or has an unseen notify-kind event.
- **Empty parks are not scanned.** `notification_park.has_parked()` and the registry's `has_parked_events()` check emptiness under their locks before running a claim scan.
- **Kanban collection no longer binds the provider scope on empty ticks.** Collection runs under a lightweight home override only. The full profile runtime scope (provider/terminal bindings) is entered only when texts or claims exist. If that binding fails, collected claims are released for retry next tick instead of killing the poller. Finalized, closing or taken-over sessions collect nothing.
- **Board change detection uses a commit revision.** The per-board signature is `PRAGMA data_version` from a retained read-only connection, not `(mtime, size, inode)` of `kanban.db`/`-wal`. Stat can miss a WAL publication. The memo is keyed by session and is set only after `board_quiet` proves the chat has nothing outstanding.

## How it works

- `tui_gateway/notification_wait.py`: `generation(queue)`/`wake(queue)` maintain `queue._notification_generation` under the queue's mutex and `notify_all` on `not_empty`. `PollerStop` is a `threading.Event` whose `set()` also wakes. `due_timeout(now, deadlines, session, stop)` returns time to the earliest deadline, capped at 0.5 s when work is held or the stop event is a plain `Event`.
- `tools/process_registry_awaiting.take_ready(queue, timeout, wake_generation=...)` waits only if the queue is empty **and** the generation still equals the poller's snapshot taken at the top of the loop. A wake between "inspect sources" and "wait" therefore cannot be lost.
- Producers that wake: `notification_park.park` (when admitted), `ProcessReadoptMixin` when parking an event, and `_notif_hold_for` when another thread hands over a held event (a self re-hold by the poller's own thread does not wake, so admission/backoff refusal still waits).
- `tui_gateway/notification_idle.py`: `state_revision(db)` is `kanban_replica_observer.fingerprint(db_path)`, or `None` while the writer is in a transaction. `pending_producers`, `schedule_manager` and `board_quiet` hold the empty proofs in the session dict (`_notif_producer_empty`, `_notif_schedule_memo`). A manager is reused only when the raw metadata row is absent **and** `manager.state is None`. A swallowed read error is never cached as absence. `collection_home` sets the Hermes home override to the session's `profile_home` (or the launch home).
- `tools/async_delegation_idle.ledger_quiet(path)` keeps up to 32 thread-local read-only (`mode=ro`) connections, keyed by path and checked against `(st_dev, st_ino)`. It caches only a quiet `data_version`, and fails open (returns `False`) on errors or a missing file. It never creates or migrates the ledger.
- `_notif_poll_kanban` splits into `_notif_collect_kanban` (home-scoped) and `_notif_poll_kanban_scoped(sid, session, prepared)`. Custody (`_kanban_pending`, `_kanban_claims`) is recorded before provider binding.

## Code

- New: `tui_gateway/notification_wait.py`, `tui_gateway/notification_idle.py`, `tools/async_delegation_idle.py`.
- Changed: `tui_gateway/session_notifications.py` (poller loop, `_kb_board_file_sig`, `_kb_poll_board`, `_notif_poll_kanban`, `_notif_collect_kanban`, `_notif_hold_for`, `_notif_take_parked`, heartbeat/loop tick, `_start_notification_poller`), `tui_gateway/notification_park.py`, `tui_gateway/producer_admission.py`, `tools/async_delegation.py`, `tools/process_registry_awaiting.py`, `tools/process_registry_readopt.py`.

## Tests

Listed from the patch. Not rerun in this mirror pass.

- `tests/tools/test_async_delegation_idle.py`: `test_unchanged_empty_ledger_skips_orphan_scan`, `test_replacement_and_errors_never_reuse_quiet_proof`, `test_commit_racing_scan_is_rechecked`, `test_commit_invalidates_quiet_proof_and_pending_is_never_cached`.
- `tests/tui_gateway/test_notification_idle_gates.py`: `test_unchanged_schedule_does_not_query` and `test_schedule_commit_invalidates_empty_proof` (heartbeat and loop), `test_unchanged_empty_producer_does_not_query`, `test_producer_commit_invalidates_empty_proof`, `test_empty_kanban_poll_does_not_bind_provider_scope`, `test_board_revision_observes_same_stat_commit`, `test_board_quiet_ignores_non_notify_but_not_inflight`, `test_empty_registry_park_skips_scan`.
- `tests/tui_gateway/test_notification_poller_idle.py` (drives the real `_notification_poller_scoped_loop`): `test_idle_production_poller_waits_until_due_and_stops`, `test_production_poller_queue_put_delivers_within_half_second`, `test_production_poller_park_publication_delivers_within_two_seconds`, `test_self_rehold_does_not_invalidate_wait_generation`, `test_collected_claims_release_on_provider_binding_failure`, `test_finalize_during_provider_binding_releases_prepared_claims`.
- `tests/tui_gateway/test_notification_wait.py`: `test_stop_wakes_waiter_and_pre_wait_stop_is_not_lost`, `test_queue_put_wakes_long_wait_and_keeps_custody`, `test_held_handoff_wakes_all_waiters`, `test_wake_releases_multiple_actual_waiters`, `test_held_work_keeps_busy_to_idle_latency_bounded`, `test_both_park_producers_publish_a_wake`.

```sh
scripts/run_tests.sh tests/tools/test_async_delegation_idle.py tests/tui_gateway/test_notification_idle_gates.py \
  tests/tui_gateway/test_notification_poller_idle.py tests/tui_gateway/test_notification_wait.py
```

No CPU measurement accompanies this patch. The tests prove skipped reads and wake latency bounds, not a gateway-wide CPU figure.

## Applying

Touches shared poller code in `session_notifications.py` that many earlier patches edit. Apply only through the snapshot-bound replay in [README.md](README.md).

## Gotchas

- Any new out-of-queue producer that hands work to a poller must call `notification_wait.wake(process_registry.completion_queue)` after publishing. Otherwise an idle poller sleeps until its next deadline.
- `_start_notification_poller` must keep creating a `PollerStop`. A plain `threading.Event` falls back to the 0.5 s cap (safe but not idle) and its `set()` does not wake the wait.
- Pending async-delegation rows and foreign in-flight Kanban claims are never cached as quiet. Their readiness depends on clocks and owner liveness.
- The placeholder-session check in `_kb_poll_board` now runs after the signature lookup. A placeholder session still returns `[]` before any memo is stored, so it never takes the unchanged-board shortcut.

## Plugin?

No. The poller loop, completion queue and park live in the core TUI gateway; plugins only feed events through the existing registry.
