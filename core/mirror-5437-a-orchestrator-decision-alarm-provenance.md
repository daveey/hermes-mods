# Agent-owned orchestrator decisions and Board alarm provenance

Mirror entries: 5409, 5410, 5411, 5412.

## Behavior

- A worker's unanswered question to its orchestrator (`decision_requested`) stays agent-owned. Elapsed time, a refused or failed wake (quota, offline, error), or a missing chat subscriber no longer turns it into a blocked `needs_input` card with a human request.
- The card keeps its `scheduled` status, its original answer probe and deadline. One `decision_unanswerable` observation per ask is recorded, marked as agent-owned with a retry-delivery recovery note; repeated probe ticks do not add more.
- The unloaded-chat sweeper never acknowledges or writes a passive row for such an ask, even past the two hour interval. A failed wake rewinds the notify cursor so the next pass retries.
- The notice text tells the orchestrator to keep agent recovery on quota or offline failures and to escalate to the user only for a concrete human-only action, after the try-to-unblock and generate-unblock-card steps, with scope and evidence.
- An explicit human-only block, written through `block_task` with a complete human request, still blocks.
- Board alarm provenance: a new server-owned `tasks.alarm_source` column is pulled by sync but never captured or pushed. Pickup-alarm source exclusion and the stall repin grant read it independently of `created_on`, so re-homing a card does not change which machine the alarm excludes. A NULL value falls back to `created_on`; an empty string is an explicit refusal.
- Before the sync migration adds the column, the local dispatch prefilter projects `NULL AS alarm_source`, so legacy filtering still works.

## How it works

`kanban_decision_ask.escalate` now runs inside one write transaction: it requires a still-`scheduled` card, respects an answer already seen since the park (unless the caller just ran the card's own probe), requires an existing ask event, and skips if a `decision_unanswerable` event already follows that ask. It then appends the agent-owned event and returns true; it never changes status or clears the probe. `owner_fallback` and `escalate_if_overdue` call it with the same meaning.

In `tui_gateway/kanban_unloaded.py`, `_deliver_sub` tags asks in notice metadata. When budget or slots refuse the wake, it records recovery once the ask is overdue, then always rewinds the cursor and returns. `_wake_job` rewinds instead of persisting a passive row when a `decision_requested` wake does not start.

For provenance, `refusal` in `kanban_pickup_alarm_identity` and `alarm_own` in `kanban_pickup_stall_repin` compare the key's source against `alarm_source`, falling back to `created_on` only when it is NULL. `kanban_db_sync` adds the column to `SERVER_COLUMNS` and the sync DDL. `kanban_worker_node.local_refusals` selects the column, or `NULL AS alarm_source` when the table lacks it (5412). Entry 5411 is a test-only fixture alignment that 5412 then reverts to the original minimal schema; the net effect is the 5412 state.

## Code

- `hermes_cli/kanban_decision_ask.py`
- `tui_gateway/kanban_unloaded.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_pickup_alarm_identity.py`
- `hermes_cli/kanban_pickup_stall_repin.py`
- `hermes_cli/kanban_worker_node.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Decision tests cover: no-subscriber owner keeps the scheduled card; overdue ask retains the probe and retry custody; quota, offline and no-subscriber causes never create a human request, leave probe state and park payload unchanged, and record exactly one observation; an explicit human-only approval request still blocks; failed wakes (quota, offline, error) are retried on the next sweep with no row written. The rewritten `notice_text` wording is untested.

Provenance tests cover: refusal and repin decisions for NULL, matching, and empty `alarm_source` against rehomed `created_on`; `alarm_source` pulled but not pushed or captured; the stamp reaching the local prefilter; and a fake Board stamping the field on detector-created cards. The source-exclusion test keeps the legacy minimal schema (no column) passing. The `SERVER_COLUMNS`/DDL change is only covered indirectly through the sync tests.

Source test paths:

- `tests/hermes_cli/test_kanban_decision_ask.py`
- `tests/hermes_cli/test_kanban_alarm_source.py`
- `tests/hermes_cli/test_kanban_sync_apply.py`
- `tests/hermes_cli/test_kanban_pickup_alarm_placement.py`
- `tests/hermes_cli/test_kanban_pickup_source_exclusion.py`
- `tests/hermes_cli/kanban_sync_fake_board.py` (fixture)

## Applying

Apply these entries only as part of the **whole ordered 5437 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches in isolation.

## Gotchas

- Without a human-authored escalation, an ask nobody can answer stays scheduled indefinitely; the single `decision_unanswerable` event is the only signal.
- `UNANSWERED_KIND` is now diagnostic only; consumers must not treat it as a block.
- `alarm_source` is stamped by the Board service. A Board that does not stamp it leaves NULL, which falls back to `created_on` and so keeps the old rehome sensitivity.
- Entry 5411 alone leaves the prefilter query dependent on the new column; 5412 is what makes pre-migration databases work.

## Why core instead of a plugin

Card status transitions, the notify cursor, the wake budget and sync column ownership live in core transactions and the sync protocol. A plugin cannot keep a failed wake from being consumed or declare a column server-owned.
