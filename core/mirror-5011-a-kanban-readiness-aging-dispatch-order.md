# Kanban shared readiness aging, inherited urgency and aged admission order across dispatch consumers

Mirror snapshot 5011; ordinals 4373, 4374, 4375, 4376, 4413, 4426.

Entries:

- **4373**: fix(kanban): share readiness aging and inherited urgency across dispatch
- **4374**: test(kanban): reconcile current pickup and queue consumers with aging
- **4375**: test(kanban): align full CI fixtures with readiness aging and priority audit
- **4376**: fix(kanban): preserve source creation order for equal ready scores
- **4413**: Align claim screening and board shares with aged admission order
- **4426**: Skip unused placement backlog ranking during dispatch

## Behavior

- **Shared scoring (4373)**: every dispatcher, the queue-position/explain commands and the priority audit use one configured scoring policy: readiness age plus inherited urgency (dependency-graph and advisory turn-urgency floors; alarm cards get a floor of 900). Ready and review lanes are ranked together as a combined order; explain output shows combined and lane position and the score breakdown. A review card is only ranked when review dispatch is enabled. Tool schemas and generated-priority guidance describe inheritance for created cards.
- **Consumer reconciliation (4374, 4375)**: tests for pickup, label-backlog waits, parent-first dispatch, host cap, priority cap and the hub-admission audit are updated to the aged order.
- **Equal-score order (4376)**: equal ready scores keep source creation order (deterministic FIFO), with ready-episode precedence and local custody intact.
- **Claim screening and board shares (4413)**: the hub claim screen now ranks frozen ready+review lanes with the same ordering so a review card reached first is not deferred behind lower-ranked ready cards that spend the judge budget. Cross-board shares score every eligible card before the cut with one shared tick timestamp; across boards a card's age counts from the later of its ready/review entry and its board's latest worker start, and an unreadable score falls back to raw priority. The 2 h starvation guard stays as an independent per-board minimum-service guard.
- **Backlog ranking skip (4426)**: `note_unplaceable` asks placement for a lane verdict without paying for a complete label backlog ranking (`backlog=False` opt-out; keyword defaults keep compatibility). No event or eligibility semantics change.

## How it works

- `kanban_admission_score.rank` is the single ranker; `_source_snapshot` reads graph and history together inside a savepoint without committing a caller-owned transaction, merging local and replica sources with local custody winning. Missing producer-identity columns on minimal replicas give no urgency floor.
- `kanban_dispatch_priority.py`, `kanban_effective_priority.py`, `kanban_turn_urgency.py`, `kanban_queue_position.py`, `kanban_admission_pools.py`, `kanban_board_share.py` and `kanban_hub_admission_claim.py` consume it.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_admission_explain.py`
- `hermes_cli/kanban_admission_pools.py`
- `hermes_cli/kanban_admission_score.py`
- `hermes_cli/kanban_board_share.py`
- `hermes_cli/kanban_db_creation.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_preclaim.py`
- `hermes_cli/kanban_db_edit.py`
- `hermes_cli/kanban_db_graph.py`
- `hermes_cli/kanban_dispatch_priority.py`
- `hermes_cli/kanban_effective_priority.py`
- `hermes_cli/kanban_hub_admission_claim.py`
- `hermes_cli/kanban_queue_position.py`
- `hermes_cli/kanban_turn_urgency.py`
- `hermes_cli/kanban_worker_node.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/gateway/test_kanban_cross_board_slots.py`
- `tests/hermes_cli/test_kanban_capacity_order.py`
- `tests/hermes_cli/test_kanban_dispatch_placement_backlog.py`
- `tests/hermes_cli/test_kanban_edit_priority_lower.py`
- `tests/hermes_cli/test_kanban_effective_priority.py`
- `tests/hermes_cli/test_kanban_fair_dispatch.py`
- `tests/hermes_cli/test_kanban_fair_explain.py`
- `tests/hermes_cli/test_kanban_host_cap.py`
- `tests/hermes_cli/test_kanban_hub_admission.py`
- `tests/hermes_cli/test_kanban_hub_admission_claim.py`
- `tests/hermes_cli/test_kanban_hub_claim_wake_race.py`
- `tests/hermes_cli/test_kanban_label_backlog_wait.py`
- `tests/hermes_cli/test_kanban_parent_first_dispatch.py`
- `tests/hermes_cli/test_kanban_pickup_held_budget.py`
- `tests/hermes_cli/test_kanban_pickup_local_order.py`
- `tests/hermes_cli/test_kanban_pickup_pinned_first.py`
- `tests/hermes_cli/test_kanban_priority_audit.py`
- `tests/hermes_cli/test_kanban_priority_cap.py`
- `tests/hermes_cli/test_kanban_score_sources.py`
- `tests/hermes_cli/test_kanban_turn_score.py`
- `tests/hermes_cli/test_kanban_turn_urgency.py`
- `tests/hermes_cli/test_kanban_urgency_floor.py`
- `tests/plugins/test_kanban_foreign_card_api.py`
- `tests/test_kanban_hub_admission_audit.py`
- `tests/tools/test_kanban_create_priority_inheritance.py`
- `tests/tools/test_kanban_generated_priority_guidance.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Eligibility and capacity limits still apply after ranking; the score only orders candidates.
- Replica and local databases cannot share an atomic snapshot, so a read can mix sources; local wins on conflict.
- Alarm urgency is keyed on idempotency keys, so cards created without them get no floor.
