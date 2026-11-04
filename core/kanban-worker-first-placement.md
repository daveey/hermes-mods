# Opt-in worker-first portable placement

Patch **1662** (`91cbdec68d886d5965cd23fa2646cc6002a5d205`). Extends [default placement](kanban-default-placement.md) without enabling it by default.

## Behavior

`kanban.placement.worker_pools` maps an origin machine name to a list of worker names. When the origin has an entry, eligible portable light and heavy cards created through the tool or CLI receive `placement_policy="worker-first-v1"` in their first insert. An empty or unavailable pool queues them; it never overflows onto the origin. Without an origin entry, legacy routing remains.

Explicit placement pins and `runs_on` (including `[]`) bypass automatic enrollment. Courier creation and conservative host/workspace/project/board/assignee/requirement/local-marker exclusions remain. Parents and origin metadata survive enrollment. Direct ready/review claims retain the policy fence even if sync is disabled or registry lookup fails.

## How it works

- `portable_reason()` extracts the conservative legacy portability boundary without requiring a heavy-work marker. `creation_decision()` runs pool enrollment before ordinary routes. `validate_creation()` rejects unknown policies or contradictory manual/host placement.
- The Board owns assignment. `Hub.reserve()` posts `task_id`, `expected_revision`, `policy` and `candidates` to `/v2/boards/<board>/placement/reserve`; `Hub.repin()` provides explicit revision-checked rollback. The client reevaluates only acknowledged origin-owned ready cards, with no retained lease or pending task write. A reserve response never directly installs an assignment: an authoritative pull does.
- Each reevaluation attempts at most four cards. A durable `worker_reserve_cursor` rotates refused cards; transport failure stops the batch after advancing only the attempted card. Dispatch retries before its own local spawn-budget check, with a shared two-second HTTP deadline, including reused sockets. Placement failure does not stop unrelated local dispatch.
- `execution_refusal()` requires primary sync and exactly the local `machine:<gateway>` assignment. The Board lease is still mandatory. Before opening a run, `local_admission()` samples pressure and whole-host occupancy; it requires finite valid signals, a positive integer capacity, a free slot and `max(cpu_load1/cpus, mem_used_pct/100) < 0.85`. Refusal releases the unused grant.
- `active_card_runs()` reads the shared execution root and named boards across profiles, deduplicates physical DBs and excludes foreign lease replicas. Unreadable/unknown state returns `None`, not spare capacity. Metrics always send the field, including null.

## Code

New `hermes_cli/kanban_worker_placement.py`; integration in `kanban_placement.py`, `kanban.py`, `kanban_db.py`, `kanban_db_connect.py`, `kanban_db_sync.py`, `kanban_db_dispatch.py`, `kanban_db_dispatch_guard_notice.py`, `kanban_worker_node.py`, `kanban_sync_client.py`, and `machine_metrics.py`. Tool entry points and schema: `tools/kanban_tools.py`, `kanban_tools_placement.py`, `kanban_tools_schemas.py`. The patch also preserves extracted model-whitespace validation in `kanban_pin_provider.py` and `kanban_model_assignment.py`. Configuration/rollout reference: `cli-config.yaml.example`, `hermes_cli/config_defaults.py`, `docs/worker-first-v1.md` in the source tree.

## Tests

All paths below are under `tests/hermes_cli/` unless noted. These are source tests present in the patch, **not rerun in this mirror**:

| Contract | Exact test reference |
|---|---|
| Tool first-insert policy and queue response | `test_kanban_placement.py::test_tool_creates_worker_policy_atomically_and_reports_queue` |
| CLI pins and enrollment | `test_kanban_worker_policy_cli.py::test_cli_pool_create_preserves_pins` |
| Light/heavy portability, pins, legacy/courier exclusions | `test_kanban_worker_placement.py::test_portable_reason_accepts_light_and_heavy_scratch_work`, `test_portable_reason_retains_conservative_exclusions`, `test_pool_precedes_gateway_and_heavy_routes_and_never_falls_back`, `test_explicit_pins_bypass_worker_policy`, `test_hub_bound_work_keeps_legacy_route`, `test_courier_and_host_work_bypass_worker_policy` |
| Atomic policy, direct ready/review fences and admission | `test_kanban_worker_policy_db.py::test_policy_is_captured_in_first_insert_with_parent_and_origin`, `test_queue_fence_precedes_direct_claim_and_lease_even_without_sync`, `test_local_dispatch_refusal_survives_registry_failure_and_disabled_policy`, `test_worker_local_admission_rechecks_load_and_releases_unused_lease`, `test_admission_sampling_failure_releases_grant`, `test_unknown_policy_is_rejected_before_insertion` |
| Post-ack sequencing, authoritative pull, rollback fence | `test_kanban_worker_reserve_client.py::test_sync_tick_reserves_only_after_task_push_ack`, `test_reevaluation_sends_revision_but_never_assigns_from_response`, `test_acknowledged_policy_sync_does_not_replay_stale_assignment_fields`, `test_reevaluation_never_moves_nonready_leased_foreign_or_manual_cards`, `test_disabled_or_invalid_pool_retains_existing_fence`, `test_reserve_http_contract` |
| Bounded failure handling and fairness | `test_kanban_worker_placement_failures.py::test_hanging_reserve_times_out_and_local_work_spawns_same_tick`, `test_http_budget_is_shared_across_requests`, `test_reservation_batch_is_bounded`; `test_kanban_worker_reserve_fairness.py::test_persistent_refusals_cannot_starve_later_cards`, `test_transport_stop_advances_only_the_attempted_card` |
| Whole-host occupancy, fail-closed telemetry | `test_machine_occupancy.py::test_occupancy_counts_all_profiles_and_named_boards_but_not_foreign_leases`, `test_corrupt_board_makes_occupancy_unknown_not_zero`, `test_collect_reports_unknown_as_null_to_clear_old_sample` |
| Integration follow-ups | `test_kanban_guard_hold_sync.py::test_home_maintains_guard_hold_on_worker_queued_policy_card`; `test_kanban_sync_hub_keepalive.py::test_board_deadline_caps_every_kept_alive_request` |

Client fake-service tests do not establish server atomicity, deployed capability attestations, credential access or live multi-worker distribution. The patch's source rollout document cites separate Board server tests; that server implementation is not contained in this core patch. No direct `execute_code` creation acceptance is supplied here.

## Applying

Replay 1662 with the [ordered series](README.md). Before enabling pools, deploy matching Board routes/schema and current clients on **every claiming gateway**, then verify capacity, fresh metrics, explicit worker pickup and operator-owned profile/capability/skill attestations. This patch activates no configuration by itself.

Rollback removes new enrollment, not existing fences. Keep the Board routes available until queued cards are drained or explicitly repinned through the creating gateway with their current revision. Pull and verify `placement_policy=null` and the requested labels before execution. Let running/review leases finish; revert the Board server last.

## Gotchas

Pool membership is a candidate list, not an AND-list of `machine:*` requirements. Load average is not CPU utilization. Removing config or changing sync mode must not make existing policy cards locally runnable. Older Board servers can reject `active_card_runs`, leaving telemetry stale even when pools are disabled. Never erase placement fences in SQLite to work around a rollback. This is core routing/lease logic, not a plugin.


## Follow-up 1841–1843: ordinary repo work enrolls worker-first; host-bound work stays home

Patches: **1841** (`9328cbc2d9`, `fix(kanban): let ordinary repo work and MacBook-placed cards enroll worker-first [daveey]`), **1842** (`dfafe9cb8c`, `fix(kanban): keep host skills, local worktrees and hermes-hub runs on their host [daveey]`), **1843** (`46329eeaba`, `fix(kanban): keep every home-rooted .worktrees tree on its host [daveey]`). Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

### Behavior

- Ordinary repo work and MacBook-placed cards can enroll worker-first instead of all queueing on the hub (1841).
- Cards that need host skills, a host-local worktree or a hermes-hub run stay on their host (1842), and every home-rooted `.worktrees` tree counts as host-local, not only the hermes-agent checkout's (1843).

### How it works

`hermes_cli/kanban_placement.py` loosens the portability boundary and adds explicit host-bound markers (host skills, `LOCAL_WORKTREE_PATH` for any `~/**/.worktrees`).

Source commit notes (first paragraph of each message, privacy-normalized):

- 1841: Problem: 2026-11-03 06:56 UTC the hub held 84 ready cards and ran 6-7 locally at its cap of 7, while worker-1 (max_card_runs 16) ran 2. Only 1 ready card carried placement_policy=worker-first-v1.
- 1842: Review of d2f8d4fd (PR773) named wrong-host risks in the loosened portability boundary. Traced on hermes-hub:
- 1843: Re-review of 15ef9d20 (PR773, B1): LOCAL_WORKTREE_PATH covered only ~/.hermes/hermes-agent/.worktrees, so cards naming the per-repo kanban trees on the hub enrolled worker-first though their work exists only there (~/code/myapp/.worktrees/t_50dec368: 1 unpushed commit; ~/code/askeve/.worktrees/t_01a18c8*: 12 trees, 1-6 dirty files each). Repro at 15ef9d20: decide() returned worker-first-v1 for both titles.

### Code

`hermes_cli/kanban_placement.py`.

### Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_placement.py`: `test_hub_origin_heavy_repo_work_goes_to_the_worker`
- `tests/hermes_cli/test_kanban_worker_placement.py`: `test_ordinary_repo_work_enrolls_worker_first`, `test_hub_state_work_stays_local`, `test_placement_note_from_the_origin_is_not_a_host_need`, `test_placement_note_does_not_hide_a_real_host_need_in_the_card_text`, `test_host_specific_state_and_identity_stay_local`
