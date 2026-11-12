# Worker pickup: local ordering and full event history

Patches: **2439, 2459, 2478**.

## Behavior

- **2439**: when a worker node picks up foreign cards under a per-tick budget, candidates are first ordered by the executor's own, possibly unpublished, local priority graph (links, unlinks, done/archived parents) instead of the stale replica order. Review-lane cards stay ahead of ready-lane cards, and each lane is ordered separately.
- **2459**: pickup copies a card's complete `task_events` history, in source insertion order, instead of only the newest 200 (`EVENT_TAIL` was removed). Policy evidence such as an authenticated continuation or its invalidation can sit arbitrarily far back behind queue diagnostics, and a newest-first copy also reversed local event ids.
- **2478**: copies picked up before 2459 stay truncated, which made a ship/land gate refuse a long card because early admission events were missing. A new refill repairs such copies from the replica. It runs on every pickup tick and on demand via `hermes kanban sync refill-history [TASK_ID ...] [--json]`. The CLI exits 1 when a named card could not be fully refilled, and 2 when the board does not sync or has no replica yet.

## How it works

`kanban_worker_node._evaluate` applies `kanban_dispatch_priority.order_with_remote_waiters` per lane (review, then ready) before spending the import budget. `_changes_for` imports `task_events` with `ORDER BY id`, like runs and comments. `kanban_pickup_history.refill` handles only foreign open copies (`created_on` is another gateway; this gateway's own cards are authoritative). It fills only the missing prefix, meaning replica events older than the earliest event the copy already holds, matched by `uid`. Rows keep `uid`, `kind`, `payload`, `created_at`; the run is resolved by run uid, and an event whose run is not local is skipped and counted `unresolved`, never re-homed. Refilled rows get negative local ids below all existing ones (oldest lowest), so they are never "the latest X" and never cross a notifier cursor (no re-sent notifications). Inserts run in one write transaction with capture suppressed (`kanban_sync_state.applying`), so nothing is pushed back to the Board. `pickup` calls `_refill_copies` after `evict`; a refill failure is logged and never stops a pickup. Related: [kanban-pickup-priority-donation-2409b.md](kanban-pickup-priority-donation-2409b.md), [kanban-pickup-evidence-grace-2409a.md](kanban-pickup-evidence-grace-2409a.md).

## Code

`hermes_cli/kanban_worker_node.py`, `hermes_cli/kanban_pickup_history.py` (new), `hermes_cli/kanban_sync_cli.py`, `hermes_cli/kanban_parser.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_pickup_local_order.py` (2439; calls `_evaluate`/`import_cards`), `test_kanban_pickup_event_history.py` (2459; `wn.pickup`, guard decisions, event uid identity, `ksc.reconcile` repair of a legacy truncated copy, real dispatch claim), `test_kanban_pickup_history.py` (2478; pickup with more than 200 events, refill of a running copy, no invented admissions, the `hermes kanban sync refill-history` handler `_refill_history` via `kanban_sync_cli` with captured output, per-tick refill). The CLI test calls the handler directly; argparse registration in `kanban_parser.py` is not separately tested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Refill only fills the prefix before the earliest held event; a newer gap is left to the sync feed to avoid racing it.
- Terminal (`done`/`archived`) copies are not refilled, since gc trims their events.
- A card whose replica holds none of the copy's events has no anchor and is reported as `no anchor event in the replica`.
- Negative local event ids are intentional; code that assumes positive ids for the oldest events would be wrong.
- Refill is idempotent and safe on a running card.

## Plugin

Core change; no plugin required.
