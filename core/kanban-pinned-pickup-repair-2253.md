# Pinned pickup evidence and stalled-work repair

Patches: **2222**.

## Behavior

Pickup records why a card pinned to this machine was not imported. Eligible foreign-created ready cards that remain unimported beyond `kanban.sync.pickup_stall_seconds` (default 600 seconds) receive agent-owned repair work, not a human request.

## How it works

The local `kanban_pickup_skips` table records first/last observations, reason, detail and a throttled pass count per card/reason. Reasons distinguish capacity, profile eligibility, capabilities, parents, holds and other failures. Even a tick with no spawn slot records evidence; capacity is blamed only when the card would otherwise qualify.

The parent-unclaimed sweep checks the full replica for old, unleased, machine-pinned ready cards missing locally. Repairs reuse open or recently closed owners. Queue, hold and registry failures are grouped per host and cause, with lower-priority repair cards naming the oldest work.

## Code

`hermes_cli/kanban_pickup_stall.py` implements evidence and repairs. `kanban_worker_node.py`, `kanban_db_sync.py`, `kanban_parent_unclaimed.py` and `kanban_db_dispatch.py` wire pickup and capped ticks. `kanban.py` and `tools/kanban_show.py` expose skip evidence; CLI triage formatters move to `kanban_cli_triage_lines.py`.

## Tests

Source coverage, **not run in this mirror**: `tests/hermes_cli/test_kanban_pickup_stall.py` covers thresholds, repair reuse/grouping, imported/other-host controls, full queues, registry/profile/parent refusals, evidence bounds, CLI not-found guidance and paused dispatch.

## Applying

Use the [README full replay procedure](README.md#applying).

## Gotchas

Evidence is machine-local, not synced; records refresh at most once per 60 seconds unless the reason changes, retain seven days and cap at 2000 rows. Repair reuse includes a one-hour closed-owner cooldown. Registry holds are not automatically lifted. Source tests cover CLI guidance and pickup internals; the tool output seam has no separate end-to-end test in this patch.

## Plugin

Core pickup and dispatch sweep; no plugin required.
