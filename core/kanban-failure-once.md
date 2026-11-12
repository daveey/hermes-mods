# One failure per run: Board release, deferred neutral effects, one final notice

Patches: **2554–2563**.

## Behavior

One failed run counts once, however many observers see it (the Board's lease alarm, the local crash sweep, the runtime cap, a stale-claim reclaim). On a Board-primary card:

- A charged failure (crash, timeout, protocol violation, terminal provider error) is no longer closed in the local mirror first. The dispatcher queues a durable run-scoped `release` command and leaves the task `running` until the Board's feed returns the closed run. A second observer of the same `run_uid` skips (already queued) instead of charging again.
- At most one `gave_up` is emitted per run, `consecutive_failures` never displays above the limit (`min(failures, limit)`), and the systemic-fix link is settled before the single final event is written.
- Each failed run produces exactly one canonical public notice, a new `failure_finalized` event carrying the final state (retry status, attempts, `tripped`). The raw `crashed` / `reclaimed` / `timed_out` / `gave_up` events from upgraded producers carry `failure_notice: "canonical"` and are silenced by chat consumers.
- Neutral outcomes (rate limited, owner restart/unavailable/down, reboot bystander) are never charged. Their observer hooks and quota effects (quota episode, notice, comment, provider wall) are deferred until the Board feed confirms the outcome. If the lease alarm won (the run ended `reclaimed` with a `reclaimed` event, or another outcome), the losing observation is retired, never replayed.
- A quota effect survives a failed wall write: the receipt stays pending, admission returns `rate_limit_cooldown`, and the retry reuses the original deadline.
- Failed-result evidence (the classifier payload, e.g. worker output and source result event) rides the release as `diagnostic`, so the Board puts it on its one outcome event.

Non-primary (local) boards keep local accounting but now share the same once-per-run receipt.

## How it works

- `kanban_failure_release`: `queue()` writes `kanban_failure_commands` (PK `run_uid`) and an outbox row with `op='release'` inside the caller's write transaction, before any mirrored row changes. `queued()` is the idempotency gate; `deferred_crash()` and (2562) `route_dead_worker()` decide whether the Board owns a classified dead worker before `close_dead_run`. Patch 2557 stamps the release with the same millisecond clock as captured rows so it cannot sort ahead of a fix card created in the same transaction.
- `kanban_sync_client.translate()` sends `release` as its own wire op; release acks don't bump the task `sync_version`, and release rows are excluded from pending-field masks in `kanban_db_sync`. A pending local run insert no longer beats a Board-ended run.
- `kanban_failure_accounting._record_task_failure` (extracted from `kanban_db_dispatch` in 2556, with `_extend_run_claim` moving to `kanban_db_heartbeat`) queues the release for primary boards, otherwise admits through `kanban_local_failure.admit()` (table `kanban_local_failures`, refuses a delayed observer once a newer run exists) and finishes with `kanban_local_failure.finalized()`. The runtime cap and stale-claim paths are fenced on `current_run_id`; stale reclaim is skipped on primary boards (the Board alarm finalizes).
- `kanban_neutral_release` records `kanban_neutral_effects` receipts; `drain()` (called from the crash sweep and from the idle reclaim phase when `pending()`) fires hooks only after the feed event exists. 2560 adds the `reclaimed`-event check, since owner restart and the alarm both end as `reclaimed`. `kanban_quota_release` (`prepare`, `finish`, `persist_wall`, `pending_task`) and `kanban_quota_episode.episode(run_id=…)` bind the episode to the captured run.
- `kanban_failure_notice`: `provisional_failure`, `event_key` (`<board>:failure:<run_uid>` dedup key), `failure_uid`, `fresh_events`. Delivery goes through the TUI notices and the gateway notifier, with per-subscription `kanban_notify_receipts` for the independent ping and wake lanes. 2561 drops the local `fresh_texts` and reuses the main branch's batch dedup. `kanban_failure_summary.describe_failure` words `failure_finalized` from `final_status`.
- 2563 passes `diagnostic=dict(dead.event_payload)` through `_account_crashes` and `_record_task_failure`, and `kanban_failed_result_observer.observe` stops waiting once a release is queued.

Related: [kanban-failed-result-reconciliation-2491.md](kanban-failed-result-reconciliation-2491.md), [kanban-quota-episodes.md](kanban-quota-episodes.md).

## Code

`gateway/kanban_watchers.py`, `gateway/kanban_watchers_notifier.py`, `hermes_cli/kanban_db.py`, `kanban_db_dispatch.py`, `kanban_db_dispatch_crash_accounting.py`, `kanban_db_dispatch_dead_worker.py`, `kanban_db_heartbeat.py`, `kanban_db_notify.py`, `kanban_db_stale_claims.py`, `kanban_db_sync.py`, `kanban_failed_result_observer.py`, `kanban_failure_accounting.py` (new), `kanban_failure_notice.py` (new), `kanban_failure_release.py` (new), `kanban_failure_summary.py`, `kanban_local_failure.py` (new), `kanban_neutral_release.py` (new), `kanban_notify_receipts.py` (new), `kanban_quota_episode.py`, `kanban_quota_release.py` (new), `kanban_sync_client.py` (all under `hermes_cli/` unless noted), `tui_gateway/kanban_notice_text.py`, `tui_gateway/session_notifications.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests):

- `tests/hermes_cli/test_kanban_real_board_failure.py`, `test_kanban_real_board_neutral.py`, `test_kanban_real_board_systemic.py` (with `kanban_real_board_bridge.py`): real Board Worker alarm, dispatcher and persisted TUI transcript. **Opt-in:** they skip unless `HERMES_TEST_BOARD_SOURCE` points at a Board source checkout with the bridge test; they were not run here. The systemic test covers fix-card custody, then resume to `ready` after the fix completes. The neutral test covers quota effects across a split feed, a failed wall write and a reopened store.
- `test_kanban_failure_transport.py`, `test_kanban_failure_once.py`, `test_kanban_failure_release_order.py`, `test_kanban_neutral_release.py`, `test_kanban_neutral_retirement.py`, `test_kanban_quota_release.py`, `test_kanban_board_bridge_lifecycle.py`, `test_kanban_sync_leases.py`, `test_kanban_sync_run_rows.py`: fake-Board sync transport and dispatcher sweep.
- `test_kanban_local_failure_receipt.py`, `test_kanban_db_stale_failure.py`, `test_kanban_failed_result_sync.py`, `test_kanban_worker_exit_trailer.py`: local receipt, stale-claim and failed-result paths.
- `tests/gateway/test_kanban_failure_delivery.py`, `tests/gateway/test_session_kanban_restart_resume_unused_grant.py`, `tests/tui_gateway/test_kanban_failure_delivery.py`: gateway notifier and TUI notice delivery.

Untested entry paths: the `hermes kanban` CLI, REST and Desktop UI do not drive these paths directly. Notices are exercised through the notifier and TUI collection functions only.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Primary boards require a portable run identity: `queue()` raises `ValueError` without one, and outside a write transaction.
- The Board service must understand the `release` op and the `diagnostic` field; an older Board will reject it.
- Tables (`kanban_failure_commands`, `kanban_local_failures`, `kanban_neutral_effects`, `kanban_quota_effects`, `kanban_notify_receipts`) are created lazily with `CREATE TABLE IF NOT EXISTS`; no separate migration.
- Observer hooks are best-effort, not exactly-once; transport ACKs are only as exactly-once as the adapter.
- Third-party consumers must treat `failure_finalized` as the public notice and ignore events marked `failure_notice: "canonical"`.
- The real-Board tests are skipped by default.

## Plugin

Core change; no plugin required.
