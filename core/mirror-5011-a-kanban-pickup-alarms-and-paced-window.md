# Kanban worker pickup: alarms queue on workers, source exclusion, and paced cards skipped in the pickup window

Mirror snapshot 5011; ordinals 4361, 4428.

Entries:

- **4361**: Queue pickup alarms on workers with authenticated source exclusion
- **4428**: fix(kanban): pickup window skips cards the local pacing governor would hold

## Behavior

- **Alarm placement (4361)**: capacity is separated from eligibility. Pickup-stall alarm cards keep normal capability/profile gates and queue on the worker pool; a free hub is never used as a fallback. Source provenance is enforced at pickup and at local dispatch (the Board companion fences authenticated creation identity and reservation/lease grants). The shared resource observer identity is preserved. A resources-unknown alarm test is included.
- **Paced window (4428)**: a worker used to copy the top N hub cards by admission order every poll even when those were held by the local pacing governor, so a runnable card of another provider never entered any worker's window. Pickup evaluation now asks a read-only twin of the pacing hold (same resolver, same pace, this machine's running count) for each otherwise-eligible card; a held card is skipped with reason `llm_paced` and takes no budget, so the window fills with the next runnable cards and stays bounded by the same budget. A skipped card is offered again on the tick its limit frees. Any fault copies as before (fail open; local dispatch still decides).

## How it works

- `kanban_pickup_alarm_identity.py` identifies alarms by explicit detector keys, never titles or priority bands. `kanban_pickup_stall.py` and `kanban_worker_node.py` carry the queue/source checks.
- `kanban_llm_pacing_dispatch.pickup_hold` mirrors `hold` without side effects and is called from the pickup `_evaluate` loop after label and quota-wall checks but before spending budget.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_llm_pacing_dispatch.py`
- `hermes_cli/kanban_pickup_alarm_identity.py`
- `hermes_cli/kanban_pickup_stall.py`
- `hermes_cli/kanban_worker_node.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/hermes_cli/test_kanban_llm_pacing.py`
- `tests/hermes_cli/test_kanban_pickup_alarm_placement.py`
- `tests/hermes_cli/test_kanban_pickup_held_budget.py`
- `tests/hermes_cli/test_kanban_pickup_paced_window.py`
- `tests/hermes_cli/test_kanban_pickup_quota_held.py`
- `tests/hermes_cli/test_kanban_pickup_source_exclusion.py`
- `tests/hermes_cli/test_kanban_pickup_stall.py`
- `tests/hermes_cli/test_kanban_pickup_stall_held.py`
- `tests/hermes_cli/test_kanban_pickup_stall_lifted.py`
- `tests/hermes_cli/test_kanban_pickup_stall_repin.py`
- `tests/hermes_cli/test_kanban_pickup_stall_reservation.py`
- `tests/hermes_cli/test_kanban_pickup_stall_resolve.py`
- `tests/hermes_cli/test_worker_resources_unknown_alarm.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Fail-open on pacing faults means a mis-evaluated card is still copied and local dispatch remains the final judge.
- Source exclusion depends on the Board-side identity fence; this repo change alone does not authenticate creators.
