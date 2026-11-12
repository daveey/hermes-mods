# Failed worker-result reconciliation

Patches: **2410–2430**.

## Behavior

A worker that records a failed result and then dies no longer stays stranded when the dispatcher or owner loop is stalled. A pre-armed, independent observer (and the normal dispatcher sweep) closes the run once the outer worker and every bound worker process are proved dead, charges crash accounting once, and appends the final events in the same board transaction. Ambiguous or malformed modern result evidence stays fail-closed (no close, including TTL reclaim); this is a hold, not proof a worker is alive. Legacy launches without two-process witnesses keep the old reclaimer policy. TTL and max-runtime expiry may still terminate a hung worker: every live exact-identity bound process of a modern-result run is signalled, but only after all witnesses are verified; any recycled, unknown or malformed witness holds the whole plan and nothing is signalled. A newer successful turn or restart checkpoint can be superseded by a later failure only when the causal snapshot is complete.

## How it works

- `kanban_failed_result.process_gone` / `failed_result_proof` / `failed_result_receipt` read only the board plus OS process identity (birth time, zombie state) and return the latest attributable failed result after the outer and all historical bound processes exit. Missing birth evidence, recycled PIDs or unreadable state are vetoes.
- `kanban_failed_result_identity` records, under the result writer transaction, the writer sequence and a snapshot of preceding bound/result events (event UIDs when synced, content fingerprints otherwise, never replica-local row ids). `attributable_events` selects by writer sequence, not import order. The same selection feeds quota, host-refusal, storage-refusal, owner-restart and worker-log classification (patch 2421). Owner restart receipts are witnessed against the actual outer launch (`owner_restart_identity_fields`).
- `kanban_run_finalizer.close_dead_run` does an exact task/run/claim/PID/birth compare-and-set inside the caller's write transaction; `_reclaim_dead_workers` and `_account_crashes` (`kanban_db_dispatch*`) compose closure and breaker accounting atomically, so competing observers and ticks cannot charge a run twice. The source result event is immutable; its time is kept separate from reconciliation time.
- `kanban_failed_result_observer` is armed before the result commits and waits for a board-open acknowledgment. It opens the board with a 250ms busy timeout and without creating or migrating it, pins the board file inode under a short generation lock (`kanban_db_boards`, `kanban_dispatch_owner`), re-checks the inode on every poll and refuses closure on an archived, deleted or recreated board. It never takes the dispatch lock.
- On Linux `kanban_failed_result_service.arm_service` starts a transient user-manager unit (`systemd-run --user`, `Restart=on-failure`, 500ms restart delay) outside the worker cgroup, resolving the command through `hermes_platform.resolver.locate_command`; an unavailable manager raises instead of claiming supervision. Other platforms launch a detached best-effort process. A total arm failure keeps the original result and records `worker_result_observer_failed`.
- Expiry (`kanban_db_worker_termination`, `kanban_owner_recovery.terminate_modern_bindings`) is fenced to the selected run, claim and exact outer birth (`expiry_identity_matches`); termination permission is separate from strict closure, which stays with the atomic finalizer.

An owner-only witnessed restart recorded before the worker bootstrap is closable by the dispatcher but does not arm an observer. The source note `docs/failed-result-reconciliation.md` exists in the source tree at the tip and has the full contract.

## Code

`hermes_cli/kanban_failed_result.py`, `kanban_failed_result_identity.py`, `kanban_failed_result_observer.py`, `kanban_failed_result_service.py`, `kanban_run_finalizer.py`, `kanban_db_dispatch.py`, `kanban_db_dispatch_crash_accounting.py`, `kanban_systemic_crash.py`, `kanban_db.py`, `kanban_db_boards.py`, `kanban_dispatch_owner.py`, `kanban_db_worker_termination.py`, `kanban_owner_recovery.py`, `kanban_host_refusal.py`, `kanban_owner_storage_refusal.py`, `kanban_quota_receipt.py`, `kanban_worker_log.py`, `gateway/session_kanban.py`, `gateway/session_kanban_restart.py`, `docs/failed-result-reconciliation.md` (all under `hermes_cli/` unless noted).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_failed_result_reconcile.py`, `test_kanban_run_finalizer.py`, `test_kanban_failed_result_service.py`, `test_kanban_failed_result_connection.py`, `test_kanban_failed_result_identity.py`, `test_kanban_failed_result_consumers.py`, `test_kanban_failed_result_observer.py`, `test_kanban_failed_result_sync.py`, `test_kanban_owner_restart_failed_receipt.py`, `test_kanban_failed_result_generation.py`, `test_kanban_failed_result_launch_safety.py`, `test_kanban_failed_result_open_contention.py`, `test_kanban_failed_result_runtime.py`, `test_kanban_expiry_signal_races.py`. These cover the board-only death proof, the observer/service launch, atomic close and accounting, generation fencing, causal receipts and expiry termination. Sync tests use an in-memory Board contract double. Forced observer restart, macOS/Windows behavior and production deployment are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The five-second closure target is an exercised healthy-observer deadline after both processes exit, not a real-time guarantee under OS starvation, a stopped user manager, repeated observer crashes, unavailable storage or write contention.
- If observer arming fails entirely, bounded closure is not promised while the dispatcher is also stalled.
- Reading `Restart=on-failure` is not a restart test; the live hub's loaded code revision must be checked separately after landing.
- Related: [kanban-failure-notices.md](kanban-failure-notices.md) covers failure notices; this doc covers the closure path.

## Plugin

Core change; no plugin required.
