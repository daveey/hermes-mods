# Kanban assign fence, pool-worker home-marker placement, held-default stall liveness

Patches: **3557–3559**.

## Behavior
- `hermes kanban assign` on a card running under another machine's live Board lease exits non-zero, names the holder, and writes nothing. After the lease expires, or when this gateway holds the lease, assign works as before.
- On a Linux pool worker, a home-gateway-marker hit no longer pins a card to its creating worker by itself. Each hit is classified: worktree and state paths are kept only if the named thing exists (and for worktrees is dirty or has unpushed commits) on this worker; hub/fleet markers queue with `capability:hub-write`; launchd markers with `capability:tailnet-exec`; ssh and token-broker markers queue plainly. "This host" and operator extra markers keep the card. Tool, CLI and direct-create entry points share the decision.
- If no single live machine holds the whole label set, or the registry is unreadable, the card stays kept and the audit records `gateway-test-failed`. An explicit `runs_on` wins over a marker.
- The held-default stall alarm no longer counts a stale or draining machine as a taker of a default-placement card, so the alarm fires for a card nobody can take.

## How it works
- 3557 adds `assign` to `kanban_remote_stop.CLI_FENCED_VERBS`.
- 3558 adds `kanban_home_marker_placement.py` (`HOME_MARKER_CLASSES`, per-hit decision, bounded `kanban_move_pool.tree_state(refresh=False)` probes: ls-remote only, at most 5 trees in a 5 s budget). Queued cards get `runs_on = [pool label, *sorted(class, coordination and sibling labels)]` plus a note; the created event gains an optional `home_marker` record. `portability()` stays probe-free; the published-worktree exemption runs first.
- 3559 factors `kanban_pickup_stall._live` (not draining, `last_seen` within `FRESH_SECONDS`) out of `_ready_now`; `others_take_default` requires it, with keyword-only `now_ms` passed from `stranded`. A full machine still counts as a taker.

## Code
- `hermes_cli/kanban_remote_stop.py`
- `hermes_cli/kanban_home_marker_placement.py`
- `hermes_cli/kanban_move_pool.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_placement_diagnostics.py`
- `hermes_cli/kanban_published_worktree.py`
- `hermes_cli/kanban_worker_placement.py`
- `tools/kanban_tools_placement.py`
- `hermes_cli/kanban_pickup_stall.py`
- `hermes_cli/kanban_pickup_stall_held.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_remote_lease_status_writes.py`: `test_cli_assign_after_the_remote_lease_expired_assigns_as_before`, `test_cli_assign_when_this_gateway_holds_the_lease_assigns_as_before`. The refusal case is the `assign` parameter of an existing status-verb test in that file; its name appears only in the commit message.
- `tests/hermes_cli/test_kanban_pool_queue.py`: `test_a_hermes_sync_card_from_a_pool_worker_joins_the_pool_with_hub_write`, `test_an_on_the_hub_kanban_db_card_from_a_pool_worker_joins_the_pool_with_hub_write`, `test_systemctl_on_a_pool_worker_joins_the_pool_with_hub_write`, `test_an_ssh_card_from_a_pool_worker_joins_the_pool_with_no_extra_label`, `test_a_review_naming_a_clean_pushed_wt_tree_joins_the_pool`, `test_a_wt_path_absent_on_this_worker_joins_the_pool`, `test_an_evidence_path_absent_on_this_worker_joins_the_pool_with_hub_write`, `test_cli_create_of_a_hermes_sync_card_on_a_pool_worker_joins_the_pool`, `test_direct_create_task_of_a_hermes_sync_card_on_a_pool_worker_joins_the_pool`, `test_a_keychain_and_systemctl_card_carries_both_reach_labels`, `test_every_home_gateway_marker_has_a_class`, `test_home_marker_hits_lists_every_hit_and_tags_operator_patterns`, `test_hub_hermes_sync_card_still_asks_the_admission_judge`, `test_pool_worker_keeps_a_home_marker_card_when_the_registry_is_unreadable`, `test_pool_worker_keeps_a_home_marker_card_when_every_pool_member_is_held`, `test_pool_worker_keeps_a_home_marker_card_no_live_machine_can_claim`, `test_a_marker_card_another_reason_keeps_stays_kept`, `test_a_marker_card_stays_kept_when_one_pool_member_lacks_the_pin_label`, `test_a_worker_keeps_a_card_naming_a_non_git_directory_under_wt`, `test_a_worker_keeps_a_card_naming_more_trees_than_the_probe_budget`, `test_a_create_probe_bounds_every_git_call_by_its_budget`, `test_a_worker_keeps_a_card_naming_its_own_unpublished_tree`, `test_a_worker_keeps_a_card_naming_its_own_artifact`, `test_a_worker_keeps_a_this_host_card`, `test_a_worker_keeps_an_operator_home_marker`, `test_a_hub_hit_with_an_unpushed_tree_stays_kept`, `test_an_explicit_runs_on_beats_a_home_marker`, `test_tree_state_without_refresh_never_fetches`, plus liveness-seam and home-spelling cases.
- `tests/hermes_cli/test_kanban_published_worktree.py`: `test_home_marker_hits_skip_a_published_worktree`.
- `tests/hermes_cli/test_kanban_pickup_stall_held.py`: `test_a_stale_or_draining_default_taker_does_not_silence_the_alarm`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- 3558 reverses an earlier control: a systemctl card created on a pool worker now queues with `capability:hub-write` instead of staying put.
- No live registry machine currently has non-explicit pickup, so 3559 changes nothing in practice yet.
- There is no kanban tool that assigns, so 3557 changes no tool handler.
