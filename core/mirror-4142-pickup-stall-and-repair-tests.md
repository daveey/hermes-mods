# Pickup-stall alarm placement and awaited repair provenance

Mirror snapshot 4142; ordinals 4120, 4132.

## Behavior

- 4120: a pickup-stall alarm is now placed on an eligible-now non-hub machine ahead of the hub, even when the hub has more free slots. Before, the hub and capable workers were ranked together by free slots alone, so an idle hub won.
- 4120: the other placement rules are unchanged: eligibility checks, the fallback order, and exclusion of the stalled machine itself. If only the hub is eligible now, it is still chosen.
- 4132: test-only. Two new negative cases show that a `claimed` event for the wrong task, or for the right task with the wrong run, does not release the `active_pr` hold on a parked owner task. Production repair code and its fail-closed gates are unchanged.

## How it works

- 4120: in `alarm_placement`, the choice among eligible-now hosts uses the sort key `(n == OPERATOR_LANE, -free_slots, name)`. The hub sorts after every non-hub host, then free slots decide, then the name breaks ties. The docstring is updated to the order: eligible-now non-hub host, eligible-now hub, hub operator lane, any other host, any card-copying host even if held.
- 4132: the test parks an owner task and records the review task's run. It deletes the owner's `claimed` events and inserts one with foreign keys that exist but mismatch one dimension. `different_task` uses the review task with the owner's parked run; `different_run` uses the owner task with the review run. It then checks `prs.active_pr_reason(...)` still returns `"active_pr"`. The legitimate review claim stays in place, so only the owner's claim is invalid.

## Code

- `hermes_cli/kanban_pickup_stall.py`
- `tests/hermes_cli/test_kanban_pickup_alarm_placement.py`
- `tests/hermes_cli/test_kanban_awaited_review_repair.py`

Mail entries in the ordered series:

- 4120: [`d718b8d2bfcaab8388ce287795f0030c3dc05a56`](patches/4120-daveey-Prefer-capable-workers-over-hub-for-pickup-st.patch)
- 4132: [`7ae71d8fb7d3a057a8651a9ff734b5450835cd5d`](patches/4132-daveey-Test-awaited-repair-claim-task-and-run-proven.patch)

## Tests

- `tests/hermes_cli/test_kanban_pickup_alarm_placement.py::test_a_queue_full_alarm_lands_on_another_eligible_machine` (changed): the registry now has a free hub plus a second worker with `active_card_runs` of 9 (little free capacity). The existing sweep-to-card assertions now require the alarm to land on the worker, not the hub. The patch message says the old code placed it on the hub.
- `tests/hermes_cli/test_kanban_awaited_review_repair.py::test_repair_rejects_claim_outside_parked_owner_run` (new, parametrized `different_task` and `different_run`): asserts `active_pr_reason` returns `"active_pr"` when the owner's claim points at the wrong task or the wrong run.
- All paths here are Python-level kanban module calls. No terminal, `execute_code`, tool, REST or Desktop entry path is claimed.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- The hub is deprioritized only among eligible-now hosts. A non-hub host that is held, draining or otherwise not ready now does not outrank an eligible-now hub.
- The changed placement test covers the queue-full case; the diff does not add a separate hold-case test for the new ranking.
- 4132 adds coverage only. It does not change how repair decides, so it cannot fix a repair bug by itself.

## Plugin?

No. Alarm placement is internal ranking logic in the core kanban module, and the repair change is a core regression test; neither is exposed through a plugin hook.
