# Kanban maintenance runs only on the card's home gateway

Patches:
- `patches/1543-fix-kanban-restrict-standing-ask-rechecks-to-card-ho.patch` (`41b7bad881c`, `fix(kanban): restrict standing ask rechecks to card home`)
- `patches/1551-fix-kanban-fence-quota-maintenance-releases-to-card-.patch` (`c968640d0c9`, `fix(kanban): fence quota maintenance releases to card home`)
- `patches/1566-fix-kanban-fence-agent-block-maintenance-to-creator-.patch` (`7c037f1086a`, `fix(kanban): fence agent-block maintenance to creator gateway`)
- `patches/1569-daveey-Keep-guard-hold-maintenance-with-the-card-hom.patch` (`91585980f6a`, `Keep guard-hold maintenance with the card home`)
- `patches/1570-fix-kanban-keep-recovery-requester-execution-at-targ.patch` (`e7a25ddec0b`, `fix(kanban): keep recovery requester execution at target home`)
- `patches/1571-fix-kanban-keep-readiness-maintenance-on-the-home-ga.patch` (`501421344ac`, `fix(kanban): keep readiness maintenance on the home gateway`)
- `patches/1572-refactor-kanban-keep-missing-parent-tracking-in-grap.patch` (`4444420bfda`, `refactor(kanban): keep missing-parent tracking in graph module`)
- `patches/1576-fix-kanban-fence-replica-triage-and-use-stable-recov.patch` (`e991e74dbea`, `fix(kanban): fence replica triage and use stable recovery park identity`)

## Behavior

On a synced board, a worker gateway keeps a local copy of a card created on another gateway (pickup, pending writes, a host-local probe). Dispatcher maintenance sweeps used to act on those copies too. Their dedup (event ids, comment checks, idempotency keys) is local to each SQLite file, so two gateways that had not synced yet could both write the same decision: two triage respawns, two fix cards, two promotions. After this series, a copy whose `created_on` names another gateway is skipped by each maintenance sweep below. Only the creator ("home") gateway makes the decision. Execution on the copy (claiming ready work, refusing a guard, local probes) is unchanged.

| Patch | Sweep fenced to home |
|---|---|
| 1543 | `sweep_standing_asks`: rechecks of standing human asks on blocked cards |
| 1551 | `release_recovered_quota_blocks`: releasing quota/hold blocks after recovery |
| 1566 | `sweep_agent_owned_blocks`: the one triage respawn for old agent-owned blocks |
| 1569 | Guard-hold cap (`block_if_guard_hold_capped`) and the once-per-hold notice and fix card (`notify_guard_hold`) |
| 1571 | `recompute_ready`: todo/blocked → ready promotion and missing-parent streak tracking |
| 1576 | `sweep_stale_triage`: stale triage supervisor creation and caps |

Two further changes keep recovery work tied to home:
- **1570:** recovery requester cards made by `ensure_recovery_cards` are pinned `runs_on=["machine:<home>"]`. Before, a portable requester could run on a worker node that had since evicted its parked copy of the target. Boards with no gateway id (unsynced) stay unpinned.
- **1576:** the recovery link event now records `park_event_uid` (the stable synced uid of the park's `blocked` event) next to the local integer `park_event_id`. `open_recovery_card` matches on the uid when it is present. A legacy link with no uid matches by integer only at home. A foreign copy treats a legacy link as a match, so it keeps the hold rather than drop it on the strength of an unrelated local id.

1569 also adds `sweep_remote_guard_holds`, run in each full dispatcher tick. For cards the home has filtered out of its own lanes (pinned to an executor), it reads the executor's synced, unbroken guard hold (`include_replies=False`) and re-checks it with `check_respawn_guard`. It then caps or notifies only if the guard reason still holds, so an expired cooldown does not look like a live hold.

## How it works

Every fence uses the existing helpers in `hermes_cli/kanban_wake_check.py`: `foreign_ids(conn)` (cards with `created_on` set and different from `own_gateway`) or `foreign_copy(conn, task_id)`. With no `kanban_sync_state` gateway id, both return empty or false, so unsynced and legacy boards (`created_on IS NULL`) behave as before. 1572 is a pure move: `_note_missing_parents` delegates to the new `kanban_db_graph.note_missing_parents`, with the logic unchanged.

## Code

`hermes_cli/kanban_block_policy.py`, `kanban_db_dispatch_quota.py`, `kanban_block_routing.py`, `kanban_db_dispatch.py`, `kanban_db_dispatch_guard_notice.py`, `kanban_resume_requester.py`, `kanban_db.py`, `kanban_db_graph.py`, `kanban_triage_sweep.py`.

## Tests

From the patches; **not run for this mirror**. Most use two gateways with real stores and sync.

- 1543 `tests/hermes_cli/test_kanban_ask_recheck_sync.py`: `test_only_home_rechecks_a_retained_foreign_ask`, `test_foreign_policy_skip_preserves_executor_resolved_probe`.
- 1551 `tests/hermes_cli/test_kanban_quota_copy_authority.py`: `test_only_home_releases_retained_copy_before_sync` (parametrized over kind and order), `test_executor_quota_stamp_still_clears_after_cooldown`.
- 1566 `tests/hermes_cli/test_kanban_agent_block_owner.py`: `test_retained_worker_block_is_escalated_only_by_creator`, `test_legacy_unattributed_block_still_escalates`.
- 1569 `tests/hermes_cli/test_kanban_guard_hold_sync.py`: `test_one_notice_and_fix_card_before_sync`, `test_cap_is_home_owned_but_executor_guard_still_refuses`, `test_pinned_executor_hold_gets_home_maintenance_in_full_tick`, `test_home_revalidates_synced_cooldown_without_executor_budget`.
- 1570 `tests/hermes_cli/test_kanban_recovery_authority.py`: `test_recovery_stays_home_after_foreign_target_eviction`, `test_unpinned_requester_cannot_resume_after_real_foreign_pickup`, `test_legacy_null_home_target_still_gets_one_local_recovery`.
- 1571 `tests/hermes_cli/test_kanban_foreign_readiness.py`: `test_retained_foreign_copy_is_not_promoted`, `test_retained_foreign_copy_does_not_maintain_missing_parent`, `test_foreign_missing_streak_is_neither_expired_nor_cleared`, `test_home_and_legacy_cards_still_promote`, `test_owner_promotes_retained_copy_then_worker_can_claim`.
- 1572: no new tests (a refactor). It relies on the 1571 missing-parent tests.
- 1576 `tests/hermes_cli/test_kanban_triage_replica.py`: `test_only_home_sweeps_or_caps_retained_copy`, `test_recovery_hold_survives_replica_local_event_ids` (legacy and uid links). `test_kanban_resume_requester_sync.py::test_foreign_park_has_one_board_wide_requester` now also asserts that `park_event_uid` is written and resolves.

## Applying

Each patch depends on the preceding series (`foreign_ids`/`foreign_copy`/`own_gateway`, the sync `uid` column, the recovery requester). Apply the full ordered series per [README.md](README.md) "Applying". Standalone application is not certified.

## Gotchas

- Home authority means the home must be online and synced. While the home is down, no gateway does maintenance on its cards: no ask rechecks, quota releases, promotions or triage. A retained copy can still claim work that is already `ready`.
- The fences key on `created_on`, not on the claim. A card created on a worker node is maintained there, even when the hub syncs it.
- Guard-hold remote maintenance covers only the `ready` and `review` lanes.
- A legacy (pre-uid) recovery link on a foreign copy always counts as open, so that copy can keep a stale hold until a uid link is written. Triage runs only at home, so this does not strand home-owned work.

## Plugin?

No. These are core dispatcher sweeps inside the Kanban DB write path, and there is no hook to filter them.
