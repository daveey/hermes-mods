# Sync parent-evidence gating, 200-op push batches, and Board CPU partition

Patches: **4567, 4605–4606, 4634** (5011 snapshot).

## Behavior

Foreign-parent mirror refresh uses trusted local and shadow P2 cursors. Only changed task/run/event rows and children of changed parents recompute; unchanged boards skip the expensive query. Fail-closed full reads and a 30-second mirror recovery floor remain. Attachment retries batch stamps and skip unchanged metadata transactions.

A Board push request never exceeds 200 ops. Unacknowledged parent cards are trimmed with rows so parents + rows fit; leftover rows go in the next batch. After every trim the fit is re-checked, because dropping a card's own row can increase the parent set.

Sync-thread CPU accounting partitions completed same-thread loop iterations into local work versus outermost Hub Board-call CPU (serialization, HTTP/TLS, retries, fallback, decoding). Wait-for-work CPU stays local. Other-thread work is never subtracted.

## How it works

Scope memos are row-keyed with a refcounted union of wanted parents. Untrusted cursors fall back to full recompute. Rollback cannot poison the memo. Connection identity prevents pairing old-handle rows with a replaced file.

`push()` trims via `_within_batch` in a loop until parents + rows fit `PUSH_BATCH`. The fake Board enforces the same cap.

`kanban_sync_cpu` publishes completed nested board services, including exceptional exits, and excludes only the outermost Hub call.

## Code

- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_move_parent_evidence.py`
- `hermes_cli/kanban_move_parent_scope.py`
- `hermes_cli/kanban_sync_attachments.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_parent_refresh.py`
- `hermes_cli/kanban_sync_cpu.py`
- `hermes_cli/kanban_sync_timing.py`
- `docs/sync-cpu-accounting.md`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_move_parent_evidence.py`
- `tests/hermes_cli/test_kanban_move_parent_scope.py`
- `tests/hermes_cli/test_kanban_move_parent_scope_queued.py`
- `tests/hermes_cli/test_kanban_sync_attachment_transactions.py`
- `tests/hermes_cli/test_kanban_sync_local_integration.py`
- `tests/hermes_cli/test_kanban_sync_parent_refresh.py`
- `tests/hermes_cli/test_kanban_sync_repair_content.py`
- `tests/hermes_cli/test_kanban_sync_push_batch_cap.py`
- `tests/hermes_cli/test_kanban_sync_cpu.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

CPU budget success is not claimed here. Prefork mixed clocks must not publish. One card has at most two parents; rows shrink each trim pass. Only explicit queued-move checkpoints and authenticated owner parks scope evidence.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
