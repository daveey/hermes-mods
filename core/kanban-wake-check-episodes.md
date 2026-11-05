# Kanban wake-check episodes: never-passing probes get reconsidered

Patches **1989–1997**. Paths are relative to the Hermes **source** tree.

| Ordinal | Source SHA | Change |
| --- | --- | --- |
| 1989 | `304c2ef7648ab4969fad234347c4cccdc8677f93` | Local episode table, 48 h stale wake, reset rules, probe-state readback |
| 1990 | `b572d1bb6d46c47ce79947a27b8929d87c84c0fa` | Lifecycle events carry episode snapshots (event-append seam) |
| 1991 | `65d0365926119d7151e15214ca07436b44adb9f5` | Per-store reset barriers; relayed probe registration keeps the episode |
| 1992 | `46096be36dd6ecbcfe24c6e733443262319fdab7` | Separate `wake_episode_evidence` event keeps other events' payloads unchanged |
| 1993 | `b9a72fc3099180fa02aee2f9194a8f4243491e05` | Probe-less park adds no evidence when no probe was ever delivered |
| 1994 | `dc089c2b593a638aa54a9c428d59326dae6da665` | In-corridor probe-less park after unknown state carries an explicit reset |
| 1995 | `1efefda9e6facb5c4f31783d5d7d910801020569` | Every probe-less re-park resets; freshness only from the local `created` event |
| 1996 | `d146b1513b5848afb8a5e41420a85d477d44d4c0` | Freshness only for locally created *triage* cards |
| 1997 | `591d4a026a024e5a93aa4a06fdba578ea876eee4` | Freshness skip only on boards with no durable sync state |

1993–1997 narrow 1992's evidence rules step by step. Several tests added in 1993–1996 were renamed or
inverted by later patches in the same range. The sections below describe the state at 1997. The Tests list
uses the test names at the frozen tip.

## Behavior

- **Stale wake.** A card parked `scheduled` with a `wake_check` whose probe exits cleanly with 1 ("not
  yet") on every check is woken to `ready` once. This happens after `kanban.wake_check_stale_hours`
  (default 48). The wake appends a `wake_check_stale` event and a `kanban-dispatcher` comment. Both
  carry the elapsed hours, the check count and the episode start in Pacific time ("… check that its
  condition can occur, then change the wait or the acceptance. Re-parking on the identical probe keeps
  this episode."). The worker context gets a "Woken to reconsider a never-passing wake check" section.
- **Interval config.** Values that are invalid, non-numeric, non-finite, zero or negative fall back to
  48 h. So does a config backend outage. The dispatcher reads the interval once per tick, before it hands
  work to the probe pool, and never inside the write transaction.
- **Episode lifetime.**
  - An identical-probe re-park through the wake → worker → re-park corridor (`scheduled`, `todo`,
    `ready`, `running`, `review`) keeps the episode's start and count. The next stale wake comes only
    after another full interval.
  - These reset the episode: a changed or cleared probe, a passing probe (including an initial pass
    whose confirmation then fails), leaving the corridor (`blocked`, `triage`, `done`, `archived`, even
    with no dispatch tick), and deleting the task.
  - Probe errors (rc 2, 124, 255, timeout, none) neither start nor advance an episode.
  - A result for an old probe generation can't count or reset.
- **Readback.** `wake_check.get_state` / `load_states` return `episode_first_at`, `episode_checks` and
  `episode_last_stale_at`. `describe_state` (the line used by `kanban show` and the worker context) adds
  "not passed since <PT stamp> (N checks)". If the episode table is missing, for example on a read-only
  board still owned by a pre-upgrade process, the old columns are still returned, with null episode
  fields.
- **Diagnostics.** `wake_check_stale` resets the ready-wait diagnostics clock, the same way the other
  wake kinds do.
- **Cross-gateway carry (synced boards).**
  - Lifecycle events carry an episode snapshot in their own payload under `_wake_episode_v1`, along
    with the per-store reset barriers under `_wake_episode_barriers`. The carrying kinds are
    `scheduled`, `continued`, `continuation_woken`, `wake_episode_started`, `wake_episode_reset`,
    `condition_met`, any `wake_*` kind, and the corridor-exit kinds `blocked`, `gave_up`,
    `timed_out`, `completed` and `archived`.
  - The first exit-1 check of an episode appends `wake_episode_started`. An unconfirmed success
    appends `wake_episode_reset`. Steady exit-1 checks update only local state and append no event.
  - A receiver consumes each snapshot once, using its own event ids as the cursor. It accepts only
    snapshots that have observed every reset it knows about. As a result, a delayed old start can't
    bring back an episode that was reset; a replayed reset receipt can't erase a newer episode;
    concurrent resets combine and clear both episodes; and an invalid barrier or snapshot is ignored
    without disturbing the event or the probe.
  - When the same episode arrives from two stores, the receiver keeps the larger count and the later
    `last_stale_at`.
  - A probe registered on another gateway (`probe_host` relay) keeps the logical probe and its episode.
- **Probe-less parks and unrelated events.** For these, other events keep their payload contract. When a
  peer might still hold an episode this home never saw, the evidence goes into a separate
  `wake_episode_evidence` event, appended just before the primary event.
  - Every probe-less re-park inside the corridor carries an explicit reset barrier.
  - One reset is carried per corridor exit. Re-entering the corridor re-arms it.
  - The one exception is the first direct `triage → scheduled` park of a triage card created on this
    board, while the board has no durable sync state. That park adds no evidence, so atomic triage parks
    still append exactly one event.
  - On a board with sync state (in any mode, including a later `off`), that park instead carries the
    reset inline in its own `scheduled` payload. That is still one event.
  - Creating sync state clears every freshness marker.

## How it works

- `hermes_cli/kanban_wake_episode.py` (new) owns the local tables. All of them are installed by
  `ensure_schema` from `kanban_db_connect.connect`:
  - `task_wake_episodes(task_id, cmd, first_at, checks, last_stale_at)` holds the episode itself.
  - `task_wake_episode_cmd` remembers the logical probe.
  - `task_wake_episode_seen` is the snapshot cursor.
  - `task_wake_episode_exit` is the one-reset-per-exit marker.
  - `task_wake_episode_fresh` is the triage freshness marker.

  SQLite triggers on `tasks` status updates and deletes keep these tables consistent, including for raw
  SQL and synced writers. `not_yet` counts a validated exit 1 and returns a stale payload when both the
  episode age and the time since the last stale wake are at least the interval. `reset` deletes the
  episode and advances the barrier.
- `hermes_cli/kanban_wake_episode_barriers.py` (new):
  - Each store has a random identity in `wake_episode_identity`. Each card has a per-task
    `{source: sequence}` vector in `task_wake_episode_barriers`. Only reset writers add entries.
  - `accept` merges remote knowledge. It clears local state on a new reset, and it rejects a snapshot
    that is missing a local reset. An empty snapshot is accepted only when it brings a new reset.
- `hermes_cli/kanban_db_events.append_event` (new seam; `kanban_db._append_event` delegates to it):
  - It calls `snapshot_event(conn, task_id, kind, payload)`, which returns `(payload, evidence)`.
  - It writes the evidence row first, when there is one, then the primary event with its recovery stamp.
  - It marks any row that carries a snapshot as consumed.
- `hermes_cli/kanban_wake_check.py`:
  - `set_check` calls `episode.repark` and takes `register_here`. A relayed probe records the logical
    command but no local execution row. `kanban_timed_park.park` passes `probe_here(...)`.
  - `apply_result` reconciles delivered snapshots before it judges a result, resets on success, counts
    clean exit 1s, and wakes with `wake_check_stale`.
  - `_SELECT` joins the episode columns. A fallback to the legacy SELECT handles boards without the table.
- `hermes_cli/kanban_db_sync.install_or_remove`: inserting the first `kanban_sync_state` row now happens
  inside the existing `write_txn`. When the insert lands, `forget_fresh` runs in the same transaction.
- `hermes_cli/kanban_diagnostics.py` adds `wake_check_stale` to `_READY_WAIT_FLOOR_KINDS`.
- `hermes_cli/config_defaults.py` adds `kanban.wake_check_stale_hours: 48`.
- `website/docs/user-guide/features/kanban.md` documents the user-facing rules (1989, 1991).

## Code

`hermes_cli/kanban_wake_episode.py`, `hermes_cli/kanban_wake_episode_barriers.py`,
`hermes_cli/kanban_db_events.py`, `hermes_cli/kanban_wake_check.py`, `hermes_cli/kanban_db.py`,
`hermes_cli/kanban_db_connect.py`, `hermes_cli/kanban_db_sync.py`, `hermes_cli/kanban_timed_park.py`,
`hermes_cli/kanban_diagnostics.py`, `hermes_cli/config_defaults.py`,
`website/docs/user-guide/features/kanban.md`.

## Tests

I didn't run these tests for this document. Each is listed from the frozen-tip source under
`tests/hermes_cli/`.

- `test_kanban_wake_episode.py` (1989). These call `apply_result` directly on a real board, with
  `schedule_task`/`claim_task` re-parks:
  - `test_always_not_yet_wakes_once_at_48_hours`: one stale event, comment text, worker context.
  - `test_pass_inside_window_resets_episode`, `test_same_probe_repark_waits_another_48_hours`,
    `test_changed_probe_starts_new_episode`, `test_exit_two_does_not_advance_episode`.
  - `test_leaving_wait_cycle_resets_even_without_a_dispatch_tick[blocked|triage|done|archived]`.
  - `test_errors_do_not_start_an_episode[2|124|255|timeout|None]`.
  - `test_clock_wake_repark_preserves_episode`, `test_cleared_probe_resets_episode`,
    `test_old_generation_cannot_count_or_reset`.
  - `test_configured_interval_and_live_state_rendering`: config file value 2 h, `load_states` and
    `describe_state`.
  - `test_initial_pass_then_failed_confirmation_resets`, `test_eviction_deletes_episode`.
  - `test_dispatcher_runs_and_persists_episode_across_connections`: through `run_due_wake_checks`, with
    the probe runner stubbed.
  - `test_readonly_legacy_probe_state_remains_visible`.
  - `test_invalid_config_uses_default[0|-1|invalid|nan|inf|None]`, `test_config_backend_outage_keeps_default`.
  - `test_stale_wake_resets_ready_diagnostics_clock`.
  - `test_pool_uses_dispatcher_interval_without_reading_config_in_transaction`.
- `test_kanban_wake_episode_carry.py` (1990). This uses an in-memory replica with a new store identity
  and copies events across:
  - `test_stale_wake_carries_age_count_and_cooldown`, `test_coalesced_blocked_transition_resets`.
  - `test_unconfirmed_success_reset_is_portable`, `test_clock_wake_carries_exact_count`.
  - `test_clean_checks_do_not_append_one_event_each`.
- `test_kanban_wake_episode_snapshot.py` (1990):
  - `test_bad_snapshot_does_not_poison_probe_or_blocked_event[...]`.
  - `test_snapshot_fields_are_validated[...]`.
  - `test_older_home_snapshot_cannot_reduce_executor_count`.
- `test_kanban_wake_episode_delayed.py` (1991):
  - `test_home_clock_wake_before_first_observation_arrives`.
  - `test_reset_before_unknown_repark_is_not_masked`.
  - `test_relayed_registration_keeps_identical_probe_episode`.
  - `test_valid_snapshot_before_malformed_event_is_not_lost`.
- `test_kanban_wake_episode_order.py` (1991):
  - `test_delayed_old_start_cannot_resurrect_after_unobserved_reset`.
  - `test_duplicate_old_reset_does_not_erase_new_episode`.
  - `test_concurrent_resets_join_without_accepting_either_old_episode`.
  - `test_changed_probe_before_start_arrives_invalidates_old_episode`.
  - `test_invalid_barrier_does_not_erase_episode[None|[]|...]`.
- `test_kanban_wake_episode_sync.py` (1991). This runs the real sync feed, apply and pickup code with
  an in-memory Board transport borrowed from `test_kanban_cross_gateway_parents`. Its docstring says
  "not live proof":
  - `test_pickup_retains_cooldown_and_publishes_next_stale`.
  - `test_feed_coalesced_reset_survives_following_unknown_same_probe`.
- `test_kanban_wake_episode_metadata.py` (1992):
  - `test_unrelated_outside_event_is_clean_and_reset_still_reaches_unobserved_episode`.
  - `test_exit_rearms_after_reentering_corridor`.
  - `test_outside_lifecycle_evidence_remains_inline[blocked|completed|condition_met|wake_check_stale]`.
  - `test_readopted_episode_outside_corridor_gets_fresh_reset`.
  - `test_probe_less_park_keeps_payload_and_clears_peer_probe`.
- `test_kanban_wake_episode_probeless.py` (1993–1996, final names):
  - `test_first_park_of_created_runnable_card_carries_a_reset`.
  - `test_direct_triage_park_of_local_triage_card_adds_no_evidence`.
  - `test_triage_card_leaving_triage_otherwise_is_not_fresh[route]`.
  - `test_triage_card_entering_corridor_is_not_fresh_without_a_further_transition[running|ready]`.
  - `test_created_runnable_card_first_park_without_status_write_resets_foreign_episode`.
  - `test_probe_park_of_triage_card_drops_freshness`.
  - `test_first_home_park_resets_undelivered_foreign_episode_on_created_card[scheduled|continued]`.
  - `test_every_probe_less_repark_carries_a_reset`.
  - `test_local_episode_without_probe_record_still_carries_evidence`.
  - `test_delivered_but_unreconciled_foreign_probe_is_cleared`.
  - `test_outside_probe_less_park_keeps_exit_reset_for_unobserved_episode`.
  - `test_in_corridor_park_resets_undelivered_foreign_episode[scheduled|continued]`.
  - `test_park_after_explicit_reset_resets_new_undelivered_foreign_episode[scheduled|continued]`.
  - `test_sync_imported_task_is_not_fresh`, `test_sync_imported_corridor_entry_is_not_fresh`.
- `test_kanban_wake_episode_triage_sync.py` (1997):
  - `test_delayed_foreign_triage_park_is_reset_by_home_triage_park[_peer_promote_claim_park|_peer_direct_park]`.
  - `test_synced_at_create_triage_park_is_one_event_carrying_the_reset`.
  - `test_enabling_sync_invalidates_markers_of_cards_created_unsynced`.
  - `test_unsynced_triage_card_keeps_the_optimization`.

The commit messages report the implementer's own results. I didn't reproduce them:

- 1989: 103 targeted tests passed.
- 1995: 65 files, 849 passed.
- 1996: 65 files, 858 passed; 7/7 mutants killed.
- 1997: 66 files, 863 passed; 9/9 mutants killed. 1997 also says the atomic-park cardinality cases in
  `test_kanban_triage_park.py` stay unchanged.

**Untested entry paths:**

- The `kanban show` CLI output and the board REST/dashboard probe state. Only `describe_state`,
  `load_states` and `build_worker_context` are called directly.
- A real multi-gateway Board deployment. Only replicas and the in-memory transport are tested.
- The `probe_host` relay through a real second gateway.
- The website doc text.

## Applying

Apply 1989–1997 in ordinal order with the rest of the series (see README § Applying). Each patch from
1990 on edits `kanban_wake_episode.py` as left by the one before it. 1990 also introduces the
`kanban_db_events.append_event` seam that 1992 extends. The patches can't be applied selectively.

## Gotchas

- **Store identity is local.** A store's identity is a random UUID created in `wake_episode_identity`.
  Copying a live database to run concurrently elsewhere duplicates that identity, and the barrier
  vectors then can't tell the two stores apart. The website doc says this isn't a supported way to
  enroll another gateway.
- **Unsynced board copied without sync.** If an unsynced board is copied to another host by a non-sync
  path (file copy or restore), it can keep a foreign episode that the skipped first triage park never
  clears. The 1997 message names this boundary. Any `kanban_sync_state` row, including mode `off`,
  counts as synced.
- **Pacific time.** The stamp in comments and `describe_state` is hard-coded to America/Los_Angeles.
- **Steady checks publish nothing.** Exit-1 checks don't publish. A peer learns the count only from the
  next carrying event (first check, reset, stale wake, re-park, wake), so a peer's count can lag until then.
- **`wake_episode_evidence` precedes the primary event.** Code that reads "the latest event" still sees
  the primary event. Code that counts all events for a probe-less park on a synced board sees one more.
- **Ask resolved_check probes are excluded.** `ask_resolved` probes (the `blocked` purpose) don't
  count episodes. `apply_result` returns before episode handling for them.

## Plugin boundary

There's no plugin equivalent. The work sits inside the core event-append transaction, in SQLite triggers
on `tasks`, and in the dispatcher's probe result path, all of which run before or below any plugin hook.
