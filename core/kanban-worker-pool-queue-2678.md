# One worker queue (`pool:worker`) for portable cards

Patches: **2585–2587, 2636, 2637, 2662, 2678, 2698**.

## Behavior

- New config `kanban.placement.worker_queue[<gateway>] = "pool:worker"`. A portable card created on that gateway (tool or CLI, no explicit `runs_on` and no pin) gets `runs_on [pool:worker, *coordination labels]` instead of the `worker-first-v1` single-machine reservation, as long as some active, unheld machine carries the label. Any worker with a free slot can then claim it, so the choice is not frozen to one machine when capacity changes. The reservation code stays for rollback. (2585)
- `heavy_route` accepts a label value (emitted as is; live when some active unheld machine has it). `hermes kanban move <id> --pool` is `--runs-on pool:worker`. Run-machine accounting treats a `pool:worker` card as POOL. (2585)
- A move to `machine:<x>` where `x` carries the pool label is refused `host_reason_required` unless `host_reason` is given (CLI `--host-reason`, tool `kanban_move host_reason`); it is recorded on the `moved` event. A pooled card moved to the worker it is already on is refused `pool_keeps_pushed` while a found tree of the card is clean and pushed (override with `force`); unpushed or dirty state is recorded as `unpushed_state`. With no tree found, `host_reason` stands. (2585, 2586)
- A pooled card whose run parks (`kanban_continue`, `kanban_schedule`, `kanban_block`, including the time and dependency parks) with a dirty tree or commits on no remote is re-pinned to `runs_on [machine:<this worker>]` in the same transaction, with a `pool_pinned` event (`unpushed_state`, `trees`). A later park with every tree clean and pushed (or gone) restores the pool (`pool_unpinned`). (2586)
- Carried inputs: a pool label in `heavy_route` or `worker_queue` now expands to every registry machine carrying it (held or idle included), so the carried-input exemption no longer switches off under pool routing. A verified carried path that names a tool no longer adds that tool's capability label to the queue `runs_on` or makes the legacy heavy route refuse. If the registry is unreadable (for example no sync identity), has no machine with the label, or `worker_queue` is malformed, the text is returned unchanged (fail closed). (2636, 2637)
- Pool attestation judges only eligible members (active, unheld, pickup on, fresh within 300 s). One held worker no longer makes every card with a derived coordination label home-only. A missing registry entry is skipped; a non-dict entry fails closed. If no eligible member remains, attestation is false. Declared access other than `hub:write` (for example `ssh:*`, `hub:read`) stays home-only with portable reason "declared access the Board cannot place". A ready local-lane card created with an old home-only verdict, when pool workers now carry its labels and have free slots, reads "Home-only by a stale creation verdict ... Re-place it (hermes kanban move <card> ...)" in queue / queue_note. (2662)
- Direct `kb.create_task` producers (swarm, triage sweep, alert cards) without `placement_checked` now queue portable cards for the pool label on a Board primary, same as the CLI and tool front doors; otherwise, or with no live labelled machine, they keep worker-pool enrollment. Explicit `runs_on` and `placement_checked` are unchanged. (2678)

## How it works

- 2585: `kanban_placement` (`POOL_LABEL`, queue decision), `kanban_placement_coordination` (`required_labels`), `kanban_worker_placement` (queue vs reservation) and `kanban_run_machine`. New `kanban_move_pool.check` returns event fields or raises `host_reason_required` / `pool_keeps_pushed`; `kanban_move.check` calls it; `workspace_unpushed` inspects the workspace and its direct child git trees.
- 2586: new `kanban_park_pin`: `plan` (git checks before the park's write txn; only the gateway whose `claim_lock` has this host's prefix plans, only on a Board primary) and `apply` (writes `runs_on` and the event while the planned run is still current). Wired in `kanban_continuation`, `kanban_db` (block) and `kanban_db_schedule`. Trees are found in the workspace and children, linked worktrees whose owner marker names the card, and trees the park text names.
- 2587: no behavior change. Move-target parsing moves into `kanban_move_pool`; `cli_runs_on` returns an explicit `--runs-on`; `kanban.py` returns to the 2000-line cap.
- 2636: `kanban_carried_evidence.without_carried_inputs` expands pool labels via `is_pool_label`; `required_labels` and `legacy_refusal` take the caller's placement cfg and strip carried inputs first.
- 2637: `without_carried_inputs` returns early when no carried-path token exists; `_label_members` returns None when the registry raises; hub admission's pool route passes cfg to `required_labels`.
- 2662: `kanban_placement_coordination.eligible()` and `attested()`; `BOARD_PLACEABLE_ACCESS = {hub:write}` is the mirror of what the Board maps (checked after `structural_reason()`); stale-verdict detection in `kanban_queue_position` and `kanban_placement_diagnostics` re-runs the full portable reason against just the eligible workers. The claim gate still rechecks the real destination; Board-side reservation is unchanged.
- 2678: `resolved_db_placement` now returns `(policy, audit, runs_on)`; `db_placement` replaces the removed `db_policy` wrapper and passes `queue=True` on a Board primary.

## Code

`hermes_cli/kanban.py`, `kanban_move.py`, `kanban_move_pool.py` (new), `kanban_parser.py`, `kanban_placement.py`, `kanban_placement_coordination.py`, `kanban_placement_diagnostics.py`, `kanban_queue_position.py`, `kanban_run_machine.py`, `kanban_worker_placement.py`, `kanban_park_pin.py` (new), `kanban_continuation.py`, `kanban_db.py`, `kanban_db_schedule.py`, `kanban_carried_evidence.py`, `kanban_hub_admission_placement.py` (all under `hermes_cli/`); `tools/kanban_tools.py`, `tools/kanban_tools_placement.py`, `tools/kanban_tools_schemas.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_pool_queue.py`, `test_kanban_pool_carried_inputs.py`, `test_kanban_coordination_placement.py`, `test_kanban_access_contract_shape.py`, `test_kanban_declared_access.py` (and a one-line edit to `test_kanban_operator_call.py`). Entry path: the placement, move and park module APIs and the create front doors with a faked machine registry; direct `create_task` placement (2678) is driven through `kb.create_task`. 2587 is a refactor with no test of its own. A real Board, real multi-machine claiming and the Board-side `worker_placement` reservation are **untested**; the claim of free-slot pickup by another worker is not proven by these tests.

## Follow-up 2698: carried-input exemption fails closed on a stale registry

- Behavior: the carried-input exemption (2636, 2637) reads the machine registry only while its last successful fetch is at most twice `REGISTRY_TTL_S` old. After a persistent registry outage the pool-label member set is no longer frozen: the exemption reports unreadable and the text is returned unchanged (fail closed), as for an unreadable registry. A short fetch blip inside that age keeps the exemption. Before, one good fetch followed by failed fetches kept serving the last snapshot, so the exemption could cover a host its receipt never verified.
- How it works: `kanban_worker_node` records `ok_at` (monotonic time of the last successful fetch, cleared by `set_registry_fetch`) and adds `registry_fresh(max_age_s)`, which returns the snapshot only when `ok_at` is within the age. `kanban_carried_evidence._label_members` calls `registry_fresh(2 * REGISTRY_TTL_S)`. `registry()` and the dispatcher's last-known-good path are unchanged.
- Code: `hermes_cli/kanban_carried_evidence.py`, `hermes_cli/kanban_worker_node.py`.
- Tests: source coverage, not run in this mirror: `tests/hermes_cli/test_kanban_pool_carried_inputs.py` (stale snapshot fails closed, a short blip keeps the exemption, the dispatcher's registry keeps its last known good value). Entry path: the carried-evidence module API and `kanban_worker_node.registry*` against a faked registry fetch and clock. Real registry outages and the create front doors under a stale registry are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Needs a Board-primary board: `runs_on` is a Board placement field, so non-primary boards keep worker-pool enrollment.
- `worker_queue` is opt-in per gateway; without it nothing changes.
- Park pinning only happens on the gateway holding the run; a block issued elsewhere never sees the worker's trees.
- `BOARD_PLACEABLE_ACCESS` must be widened by hand when the Board starts mapping more access labels.

## Plugin

Core change; no plugin required.
