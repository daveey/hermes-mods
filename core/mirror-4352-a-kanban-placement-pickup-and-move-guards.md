# Kanban placement, pickup order, home-marker audit, machine-target moves and overlap matching

Mirror snapshot 4352; ordinals 4149, 4174, 4231, 4233, 4234, 4240.

Entries:

- **4149**: Pin placement nesting-bound plain-text fallback
- **4174**: Worker pickup: machine-pinned cards first; never copy a pre-claim pin-walled card
- **4231**: home-marker audit: a pool worker's created event says queue only when the card queues
- **4233**: fix(kanban): placement config falls back to the board home's block
- **4234**: kanban move: refuse a machine: target with no host_reason naming state on it
- **4240**: fix(kanban): overlap check stops matching reviews of different PRs by template words

## Behavior

- **Pickup order** (4174): in each lane, cards pinned to this machine are picked first (stable sort, review still precedes ready), and a fresh card pinned to a model behind a quota wall is no longer copied every tick: the pre-claim hold is predicted with the dispatcher's own read-only `pinned_quota_hold` and recorded as `quota_hold "pinned_quota_wall <model> (<provider>) until <reset>Z"`. Failures copy as before.
- **Placement config** (4233): `kanban_placement.config()` uses the active profile's non-empty `kanban.placement` block when there is one (whole, no per-key merge) and otherwise the board home's block, since the board is shared by every profile on a host. Snapshots pin the fallback block.
- **Home-marker audit** (4231): a pool worker's `created` event says `queue` only when the card queues. A coordination refusal becomes keep/`gateway-test-failed`; an explicit pin carries no `home_marker`; a Linux pool worker without a queue label records keep/`no_queue`.
- **Machine-target moves** (4234): every `machine:<x>` target requires a `host_reason` naming state on the target; capacity-only reasons (free slots, N/N, full, idle) are refused and the refusal points at the pool queue. The pickup-stall alarm move gets the same rule, and the pool target no longer needs a free slot now. The operator-call move and creator repin forward an optional `host_reason`; origin repin and stranded-label reroute keep a recorded default reason.
- **Overlap check** (4240): when both card titles name a PR (`#N`, `PR N`, `PRN`, `pull/N`) and the PR sets are disjoint, the title-share reasons do not apply, so template-word overlap no longer rejects reviews of different PRs. Same PR, one-sided PR, singleton job, failure identity and board reasons are unchanged.
- **Test-only** (4149): the placement scanner's bounded nesting plain-text fallback at three and four levels.

## How it works

- `hermes_cli/kanban_worker_node.py` (`_quota_held`, `_evaluate`), `kanban_placement.py`, `kanban_home_marker_placement.py`, `kanban_worker_placement.py`, `kanban_move_pool.py`, `kanban_pickup_stall_repin.py`, `kanban_operator_call.py`, `kanban_creator_repin.py`, `kanban_parser.py` (`--host-reason`), `tools/kanban_tools_schemas.py`, `hermes_cli/kanban_overlap.py`.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_creator_repin.py`
- `hermes_cli/kanban_home_marker_placement.py`
- `hermes_cli/kanban_move_pool.py`
- `hermes_cli/kanban_operator_call.py`
- `hermes_cli/kanban_overlap.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_pickup_stall_repin.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_worker_node.py`
- `hermes_cli/kanban_worker_placement.py`
- `tools/kanban_tools_schemas.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_move.py`
- `tests/hermes_cli/test_kanban_move_machine_target_guard.py`
- `tests/hermes_cli/test_kanban_move_parent_evidence.py`
- `tests/hermes_cli/test_kanban_move_pr_handoff.py`
- `tests/hermes_cli/test_kanban_move_unpushed.py`
- `tests/hermes_cli/test_kanban_move_unpushed_followups.py`
- `tests/hermes_cli/test_kanban_move_validation.py`
- `tests/hermes_cli/test_kanban_pickup_held_budget.py`
- `tests/hermes_cli/test_kanban_pickup_pinned_first.py`
- `tests/hermes_cli/test_kanban_pickup_stall_repin.py`
- `tests/hermes_cli/test_kanban_placement_board_home.py`
- `tests/hermes_cli/test_kanban_placement_home_spellings.py`
- `tests/hermes_cli/test_kanban_pool_queue.py`
- `tests/hermes_cli/test_kanban_requires_placement.py`
- `tests/hermes_cli/test_kanban_review_checkpoint_cross_machine.py`
- `tests/hermes_cli/test_kanban_runs_on_pickup_guard.py`
- `tests/hermes_cli/test_kanban_worker_move_request.py`
- `tests/tools/test_kanban_create_overlap_pr_ids.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- Existing moves to bare `machine:<x>` now fail without a state-based reason; use the pool or give a reason. Force does not waive the check.
- Overlap PR extraction covers numbers of 2 to 6 digits; shas without PR numbers and repo disambiguation are out of scope.

## Plugin?

None.
