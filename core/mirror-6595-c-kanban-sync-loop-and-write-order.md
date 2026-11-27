# Sync loop efficiency and write ordering

**Scope:** Reader key reuse, 1s idle tick, parked event order.

**Source ordinals:** 6455, 6456, 6557, 6558

## Behavior

- The idle sync loop looks at its outboxes once per second instead of twice: a local write can reach the Board up to about one second after it is made, and stop/lease-yield is noticed within that tick. A Board change frame still returns immediately and shorter retry periods are not stretched.
- A kept reader's key string is computed once per read (absolute same-platform paths reuse `str(path)`), cutting per-query overhead without altering stat, identity, checkpoint, data-version or eviction behaviour.
- A park's event is emitted ahead of its status row and a block writes status and ask as one operation, so a replica no longer sees the new status without the event that carries its deadline. The reordering is a local-only `evidence_uid` grouping on the outbox: it applies only to a lone status op for a card the Board has already acknowledged, never to terminal statuses, claims or transactions with more than one status capture for the card, and it falls back to the legacy FIFO order if the schema is unavailable. Run ends and task patches that touch lifecycle columns keep their position. Blocking now writes `human_request` in the same UPDATE as the status (the ask is stripped of drop markers first) and then registers the resolved-check probe.
- The apply-side page writer now also covers `kanban_sync_state`, so the evidence-order triggers and schema read are prepared before the sync writer starts.

## How it works

- `kanban_sync_backlog.py`/`kanban_sync_client.py` set the wait tick; `kanban_sync_board_watch.py` holds `reader_key`.
- `kanban_sync_capture_sites.py`, `kanban_sync_evidence_order.py`, `kanban_sync_parent_order.py` and `kanban_db_sync.py` order operations within a writer transaction.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/kanban_ask_resolved.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_sync_backlog.py`
- `hermes_cli/kanban_sync_board_watch.py`
- `hermes_cli/kanban_sync_capture_sites.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_evidence_order.py`
- `hermes_cli/kanban_sync_parent_order.py`
- `hermes_cli/kanban_sync_status_origin.py`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_kanban_sync_capture_sites.py`
- `tests/hermes_cli/test_kanban_sync_evidence_order.py`
- `tests/hermes_cli/test_kanban_sync_wait_tick.py`
- `tests/hermes_cli/test_kanban_sync_watch_paths.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6455-6558, 4 commits: 6455, 6456, 6557, 6558) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- The 1-second tick is a deliberate latency trade; tests pin it.
- Evidence-order tables must exist before the first sync write.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Sync is a core engine.
