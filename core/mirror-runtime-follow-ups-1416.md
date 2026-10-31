# Runtime and maintenance follow-ups (1416–1424)

These patches cover checkpoint reclamation (1416), new-session plugin refresh
(1417–1419), advisory BuildKit cache exports (1420), managed-worker warning test
synchronization (1421), and routeless heavy-work placement (1422–1424).

## Behavior

- **Checkpoint reclamation:** `checkpoints.auto_prune: false` disables retention-based
  project deletion, not pending size reclamation. A `.gc-pending` marker permits a
  size-only maintenance pass, still subject to `min_interval_hours`. Maintenance GC
  gets a longer timeout and lower scheduling priority where `nice` is available.
  A health check reports a store above twice its cap when pruning has never run or
  is at least 48 hours old, gated by a daily marker.
- **Plugin refresh:** a plugin enabled after process startup can be discovered before
  a new agent snapshots its tools. The serve/TUI agent factory and gateway fresh-build
  branch check discovery inputs; unchanged inputs do not force discovery. After a
  gateway refresh, the agent is cached under the post-refresh signature so the next
  turn can reuse it. Creating an empty plugin directory is not a discovery change.
- **Advisory caches:** GitHub Actions BuildKit cache-export failures do not fail the
  image build step merely because its optional cache backend failed. Build and image
  push failures remain fatal; this is not blanket `continue-on-error` for the step.
- **Managed warning synchronization:** the worker-lineage regression test waits for
  queued warning publication separately from admission settlement. Patch 1421 changes
  the test only, not runtime settlement or logging semantics.
- **Routeless heavy placement:** portable heavy work can receive a configured worker
  machine target even when its origin gateway has no ordinary `route` entry. The card
  stays on the local Board and uses a machine lease, rather than becoming a courier
  creation. Explicit placement, courier intent, nonportable work and host-bound
  operations are not redirected by this fallback.

## How it works

### Checkpoint reclamation — 1416

`auto_prune_from_config()` checks for pending GC when retention is disabled and
passes `retention_days=0` and `delete_orphans=False` into periodic maintenance.
Without pending work it stays skipped, but can still call `check_store_health()`.
The config lookup defaults `auto_prune` to true when the key is absent.

Maintenance constructs `Pruner(..., maintenance=True)`. Its `reclaim()` runs Git GC
with `MAINTENANCE_GC_TIMEOUT = 1800` seconds and `low_priority=True`; `_run_git()`
uses `nice -n 10` if available. Non-maintenance reclamation retains the existing
three-times-tool-timeout budget. Size enforcement rewrites oldest snapshots
round-robin, collecting after batches of 1, 2, 4, … rewrites and any remainder,
rather than after every rewrite. It retains the last snapshot of each project and
bounds the loop to twenty rounds. The daily health marker gates the size walk;
`.last_prune` determines whether an over-cap store is stale.

### Plugin new-session refresh — 1417–1419

The manager records pre-sweep discovery fingerprints and cheap precheck stamps.
At the agent-build boundary, `refresh_if_inputs_changed()` first compares the
precheck, computes a full fingerprint if needed, joins background discovery, and
serializes forced discovery under `_GO_LIVE_LOCK` and the manager's discovery
lock. A second comparison under the locks avoids duplicating another build's pass.
An undiscovered manager is left to its normal lazy discovery.

Patch 1418 moves the public boundary helper into `plugins_refresh.py` and moves
error-classification dispatch into `plugins_error_classification.py`, re-exported
from `plugins.py`. Patch 1419 moves the gateway refresh from `_build_fresh_agent`
to `_resolve_turn_agent`'s cache-miss branch and recomputes the cache signature
when a pass ran. Snapshot and precheck logic treat missing roots and roots holding
only skipped entries as equivalent.

### Advisory BuildKit caches — 1420

The Docker build and promotion exporters and the sandbox-image exporter add
`ignore-error=true` to their `type=gha` cache destinations. Existing variant,
pull-request and architecture-scope conditions remain in place. The regression
checks exporter configuration without allowing the entire build step to ignore errors.

### Managed warning test synchronization — 1421

After the failed-worker admission reaches `unknown`, `test_worker_lineage_and_settlement`
separately awaits `Managed worker lost: managed_worker_failed` in `logs/errors.log`.
The wait uses a 30-second timeout and a 0.02-second polling interval. Existing
assertions, including absence of `invalid_worker_frame`, remain.

### Routeless heavy placement — 1422–1424

`_routeless_heavy()` requires a configured heavy target, a scratch workspace, the
default board, an eligible assignee, no uncarried options, only carried requirements,
no gateway capability need, no local-only or home-gateway text marker, a heavy-work
marker, and an eligible pickup machine. It returns `where: local` with
`runs_on: [machine:<configured-worker>]`; otherwise ordinary local placement remains.

Patch 1423 moves the explicit-pin/courier exclusion into the helper's `skip`
argument to reduce `_decide` complexity without adding a new routing policy.
Patch 1424 expands home-bound markers to include home code-checkout paths, SSH,
gateway restart wording, and `hermes config set/edit/unset`. `extra_home_markers`
can extend the built-in patterns. Gateway needs remain nonportable even if listed
in `carried_requires`.

## Code

Paths below are in the source checkout represented by the patches, not standalone
runtime modules installed from this mirror.

| Patches | Source files |
| --- | --- |
| 1416 | `tools/checkpoint_maintenance.py`, `tools/checkpoint_manager.py`, `tools/checkpoint_pruning.py` |
| 1417–1419 | `hermes_cli/plugins.py`, `hermes_cli/plugins_refresh.py`, `hermes_cli/plugins_snapshot.py`, `hermes_cli/plugins_error_classification.py`, `tui_gateway/agent_factory.py`, `gateway/session_execution.py`, `hermes_cli/web_routers/delegation.py` |
| 1420 | `.github/workflows/docker.yml`, `.github/workflows/sandbox-image.yml` |
| 1421 | `tests/gateway/test_managed_worker_lineage.py` (test-only) |
| 1422–1424 | `hermes_cli/kanban_placement.py` |

## Tests

**The following tests are present in the patches; they were not rerun in this
mirror documentation pass.** These are source-level coverage descriptions, not
new pass counts, deployment checks, performance measurements or runtime proof.

### Checkpoints

`tests/tools/test_checkpoint_size_pending.py` (1416):

- `test_over_cap_checkpoint_cost_does_not_grow_with_project_count` compares Git-call
  counts for checkpoint creation with different project counts.
- `test_periodic_prune_enforces_the_cap_with_auto_prune_off` exercises pending
  reclamation, verifies the maintenance timeout/priority arguments, and preserves
  another project's checkpoints.
- `test_auto_prune_off_without_pending_size_work_stays_skipped` covers the no-work case.
- `test_store_stuck_over_cap_logs_one_error_per_day` covers absent and stale prune markers.
- `test_store_health_quiet_when_pruned_recently_or_under_cap` covers quiet cases.
- `test_maintenance_reclaim_outlasts_three_tool_timeouts` uses simulated GC timing.
- `test_low_priority_git_runs_under_nice` checks command construction with Git's version command.
- `test_enforce_size_gcs_log_times_not_once_per_project` checks batching with fake Git operations.

These do not establish a new end-to-end CLI or gateway housekeeping result in this pass.

### Plugin refresh

`tests/tui_gateway/test_make_agent_plugin_refresh.py` (1417–1419):

- `test_new_session_build_loads_a_plugin_enabled_after_boot` exercises the serve/TUI
  factory with agent construction stubbed and checks registered hook ownership.
- `test_unchanged_inputs_run_no_forced_pass` checks repeat builds and the boundary helper.
- `test_gateway_fresh_agent_build_loads_a_plugin_enabled_after_boot` exercises the
  gateway resolver harness; 1419 adds next-turn reuse and object-identity assertions.
- `test_an_empty_plugin_dir_created_after_boot_runs_no_pass` covers an empty/skipped-only root.
- `test_an_undiscovered_manager_is_left_to_lazy_discovery` checks initial discovery ownership.

No live Desktop, SSH backend, or mid-turn refresh is exercised here. The 1418
error-classification extraction adds no dedicated new test in this patch range.

### CI cache policy and managed warning

- `tests/ci/test_artifact_quota_advisory.py::test_buildkit_cache_exports_are_advisory_without_ignoring_build_failures`
  (1420) inspects workflow exporter settings and rejects step-wide error suppression.
  It does not simulate a real BuildKit backend outage.
- `tests/gateway/test_managed_worker_lineage.py::test_worker_lineage_and_settlement`
  (1421) gains the independent warning-publication wait; the original settlement
  and warning assertions are retained.

### Heavy placement

`tests/hermes_cli/test_kanban_placement.py` (1422, 1424):

- `test_hub_origin_portable_heavy_work_runs_on_the_worker`
- `test_hub_origin_host_specific_or_light_work_stays_on_the_hub`
- `test_hub_origin_heavy_work_stays_when_the_worker_does_not_pick_up_or_is_not_configured`
- `test_hub_origin_explicit_pins_and_courier_cards_win_over_heavy_routing`
- `test_hub_origin_extra_home_markers_extend_the_host_specific_list`
- `test_hub_origin_textual_host_operations_stay_local`
- `test_hub_origin_workspace_kind_alone_stays_local`
- `test_hub_origin_gateway_need_even_if_carried_stays_local`

These test placement decisions with a supplied machine-eligibility predicate, not
actual worker pickup or execution. Patch 1423 introduces no separate test.

## Applying

Use the [core README's Applying instructions](README.md#applying) together with its
[Current inventory](README.md#current-inventory) and snapshot verification report.
The supported check is the **full ordered mirror**, using `core/verify_mirror.py`
with the matching frozen source tip, exclusion base, policy and `--replay-plan`.
That plan includes the hash-pinned upstream-merge bridge at its required boundary;
a single patch glob applied at the newest merge-base is not a substitute.

Patches 1416–1424 depend on earlier core changes and on each other within their
feature groups. Standalone application of this range is not certified. Preserve
published patch bytes and ordinals. The source object store must satisfy the
README's historical patch-index prerequisites, and successful replay alone is
insufficient without the verifier's inventory and final-tree comparison.
The full publication pass ran that verifier successfully; exact output is in
[mirror-1427-verification.md](mirror-1427-verification.md). Source feature tests
were not rerun.

## Gotchas

- Disabling retention does not promise to retain all historical snapshots: the size
  cap may still trim them. Pending reclamation is interval-gated, not an immediate
  response to every over-cap observation. Keeping one snapshot per project and the
  bounded loop mean the cap is not an unconditional storage guarantee.
- Low priority depends on `nice` availability. A long timeout is a budget, not a
  guarantee of GC completion. Daily health-check throttling depends on writing its marker.
- Plugin refresh is not a general deep-file hot reloader. The cheap precheck can miss
  a deep in-place module edit. Safe workers skip the boundary helper; plugin-load
  workers skip refresh; unreadable fingerprints do not trigger a forced pass.
  Exceptions are logged and do not deliberately fail agent construction. Existing
  cached agents are not promised immediate replacement after enablement.
- Advisory cache export does not make all CI infrastructure optional and does not
  suppress build, smoke-test or image-push failures.
- Admission settlement and warning-file publication are independent observations;
  observing the former is not proof the latter is already visible.
- Heavy placement is conservative and text-based. Host-operation wording can keep
  work local even when a caller considers it portable. A `dir` or `worktree`
  workspace stays local even without an explicit path. Machine eligibility and
  configuration are prerequisites, not proof that a lease will actually be claimed.

## Why not a plugin

Checkpoint pruning owns core Git-store history and maintenance locking. Refresh
must run before plugin discovery results are captured by core agent construction
and cache signatures; an as-yet-unloaded plugin cannot reliably install that fix.
BuildKit export policy belongs in repository workflows. The managed-worker change
belongs in the test that observes asynchronous publication. Heavy placement belongs
in the core decision path that preserves explicit intent, portability constraints
and Board lease ownership. None of these changes is a separately installable plugin.
