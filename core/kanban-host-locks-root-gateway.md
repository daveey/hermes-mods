# Kanban host dispatcher/sync locks belong to the root gateway

Patches: **1790** (`0f8b8e0a60`, `fix(kanban): host sync belongs to the root profile gateway; heal released inherited creator links [daveey]`); **1791** (`e4b3ebe805`, `fix(kanban-sync): an older core refuses to sync a board a newer core migrated [daveey]`); **1807** (`b98a7a5491`, `[daveey] kanban: retrying, root-preferring lease on the host dispatcher/sync locks`); **1808** (`9908049020`, `[daveey] update: start the root gateway first and wait for its host Kanban locks on resume`); **1809** (`1be102cf7e`, `[daveey] kanban_singleton: portable pid check, BOM-tolerant reads`); **1810** (`839e5d768d`, `[daveey] kanban_singleton: code-health waivers and logged fail-open probes`); **1811** (`8cc5cbd702`, `[daveey] ci: route gateway/kanban_singleton.py to the update suites (update_cmd_posix_pause imports it)`); **1812** (`eb153dcd53`, `[daveey] kanban sync watcher: per-tenure latching stop flag so yielded FeedWatcher threads don't revive`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Host sync belongs to the root profile gateway, and released inherited creator links are healed (1790).
- An older core refuses (`SchemaAhead`) to sync a board a newer core migrated, instead of dropping undeclared columns (1791).
- `SingletonLease` retries the host `.dispatcher.lock`/`.sync.lock` every 30 s and prefers the root gateway (1807); `hermes update` resume starts the root gateway first and waits up to 90 s for its locks (1808); portable pid check and BOM-tolerant reads (1809), logged fail-open probes (1810), CI routing (1811), and a per-tenure latching stop flag for yielded FeedWatcher threads (1812).

## How it works

`gateway/kanban_singleton.py`, `gateway/kanban_watchers*.py`, `hermes_cli/update_cmd_posix_pause.py`, `kanban_sync_client.py`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1790: Hub chats 20261016_123957_6212f7 and 20261017_114040_587d59 read unresolved/no_summary though they handed work to open cards (t_01a19084a3b36956 todo; decision card t_c21c170e). Their chat links were released=1, so session_tasks returned nothing. Five whys: - hermes-gateway-askeve-reviewer (named profile, pinned older core, no origin ledger in its HERMES_HOME) won the first-come host .sync.lock and its links reconcile released inherited created links and every root-profile ledger link each pass; - the root gateway logged 'another gateway already syncs; not starting' and never retried; - rec...
- 1791: The hub's askeve-reviewer gateway (pinned core 1f926111, before #461) held the host .sync.lock and pushed every hub card. _wire_fields drops columns the client does not declare, so worker-first cards reached the Board without placement_policy, the pull wrote NULL back, and they ran on the hub instead of a pool worker. Nothing failed loudly.
- 1807: A gateway that lost the boot-time flock on .dispatcher.lock or .sync.lock never retried, and whichever gateway reached it first kept it for life. On hermes-hub a pinned profile gateway (askeve-reviewer, old core, memory-derived max_in_progress=8 vs the root's 16) won every joint restart and held both locks for hours while 64 cards sat ready.
- 1808: A pinned profile gateway on an older core neither defers nor yields the host dispatcher/sync locks, so the lease alone can't move them off it. resume_paused_set now starts the root gateway's unit first, waits (bounded, 90s) until its new pid holds the locks its config takes (.dispatcher.lock unless dispatch_in_gateway=false, .sync.lock when kanban.sync.enabled), then starts the profile gateways; a timeout prints a warning naming the missing lock and carries on.
- 1809: (subject only)
- 1810: (subject only)
- 1811: (subject only)
- 1812: (subject only)

## Code

`gateway/kanban_singleton.py`, `gateway/kanban_watchers.py`, `gateway/kanban_watchers_common.py`, `hermes_cli/kanban.py`, `hermes_cli/kanban_chat_links.py`, `hermes_cli/kanban_db_dispatch_stuck.py`, `hermes_cli/kanban_sync_client.py`, `hermes_cli/update_cmd_posix_pause.py`, `scripts/ci/classify_changes.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/gateway/test_kanban_dispatcher_health.py`
- `tests/gateway/test_kanban_singleton_lease.py`: `test_dead_root_claim_does_not_block_profile_gateway`, `test_dispatcher_watcher_profile_gateway_yields_to_root`, `test_dispatcher_watcher_retries_a_contended_lock_then_dispatches`, `test_holder_info_names_pid_home_and_rootness`, `test_joint_restart_profile_gateway_defers_so_root_wins`, `test_long_contention_warns_naming_holder_pid_and_home`, `test_profile_holder_yields_to_root_claim_and_root_takes_over`, `test_root_detection_follows_hermes_home`, `test_sync_watcher_old_tenure_keep_running_stays_false_after_yield`, `test_sync_watcher_profile_gateway_yields_to_root_and_waits`, `test_update_resume_root_lock_wait_is_bounded`, `test_update_resume_starts_root_first_and_waits_for_its_locks`
- `tests/hermes_cli/test_kanban_creator_chat_backfill.py`: `test_a_picked_up_foreign_cards_inherited_link_keeps_the_owners_answer`, `test_a_released_inherited_link_on_an_open_card_is_wanted_again`
- `tests/hermes_cli/test_kanban_host_lock_visibility.py`: `test_at_capacity_names_where_the_cap_came_from`, `test_lock_holder_unaffected_by_unheld_sidecar`, `test_presence_reports_host_dispatcher_held_by_another_home`, `test_presence_still_warns_when_no_one_holds_the_host_lock`, `test_stats_names_host_lock_holders`
- `tests/hermes_cli/test_kanban_sync_client.py`: `test_a_named_profile_gateway_leaves_host_sync_to_the_root_gateway`, `test_root_profile_detection_reads_hermes_home_against_the_kanban_root`
- `tests/hermes_cli/test_kanban_sync_schema_guard.py`: `test_current_core_pushes_placement_policy_to_the_board`, `test_older_core_refuses_to_push_and_keeps_the_ops`, `test_older_core_round_reports_the_refusal`, `test_run_forever_gives_up_the_sync_role_on_an_older_core`, `test_schema_ahead_names_the_unknown_columns`, `test_tool_created_worker_first_card_keeps_placement_policy_on_the_board`

Untested by their own patch (refactors, constants or CI routing): 1809, 1810, 1811.

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- A pinned profile gateway on an older core neither defers nor yields; only the update-resume ordering moves the locks off it.

## Plugin?

No: these change core modules that plugins cannot hook.
