# Idle sweeps reuse structural scans until a SQLite commit

Patches **2035** (`954d6c13717b6550603d718d8041802d67b3c04c`), **2037** (`559771a2b622fc3ba1cc3b4977be74a5a35a20da`), **2038** (`ffe95d069716b1d368e249db525c0b9f1aa8a6ce`) and **2039** (`25b72fadef22b090f5e25b6c96cb9a1a25f03e38`). The notification poller counterpart (2036) is [notification-poller-commit-driven.md](notification-poller-commit-driven.md). Earlier idle gates: [gateway-idle-watcher-change-keys.md](gateway-idle-watcher-change-keys.md), [kanban-sync-repair-revision.md](kanban-sync-repair-revision.md).

Shared rule: cache only the *structural* result of a read (which rows exist), keyed on a committed-database revision. Clock predicates (grace windows, lookbacks, holder liveness, lease age) are re-evaluated every pass, transactions bypass the memo, and any read/stamp error means "scan again", never "unchanged".

## Behavior

| Patch | Sweep | Change |
| --- | --- | --- |
| 2035 | Unloaded-chat Kanban sweeper (`tui_gateway/kanban_unloaded.py`) | Lists only candidate TUI subscriptions (non-empty chat, and either in-flight or with an unseen event of a notify kind) instead of every subscription, and reuses that list across reopened sweep connections until a commit. Orphaned-claim recovery and the per-candidate event/grace/holder checks still run every pass. Stall/overdue state for subscriptions that drained or were deleted is cleared per board. |
| 2037 | Kanban notice guarantee (`tui_gateway/notice_guarantee.py`) | The candidate query no longer embeds `now`; it returns structural rows (latest active message is a `kanban_notice`) with `last_active`, which are reused until a commit. `LOOKBACK_S` and `GRACE_S` are applied in Python on every call, so a notice still becomes due or ages out with no write. Rows with no `last_active` or a non-numeric timestamp are ineligible. |
| 2038 | Remote-card notice sweep (`tui_gateway/kanban_remote_notices.py`) | After any replica commit, only task rows whose replica `seq` changed are re-projected; unchanged rows reuse their per-row card/state projection. Deleted rows drop out. |
| 2039 | Kanban sync content repair (`hermes_cli/kanban_sync_client.py`) | `repair_divergence` and `repair_missing_children` are skipped when neither the live board nor the replica committed a relevant change and mode/gateway/placement are unchanged, even if the feed cursor moved (event-only pulls). Partition link repairs, chat-link repairs and `retire_lost_placeholder_links` still run whenever the outer repair window fires. |

The commit message for 2035 records a same-input projection benchmark of about 38 ms to 0.55 ms. It is explicitly not a whole-gateway CPU claim and was not re-measured here.

## How it works

### 2035: `tui_gateway/kanban_unloaded_candidates.py`

`subscriptions(conn, kinds)` uses a retained, generation-guarded reader from `hermes_cli.kanban_reader_retention.reader(path)` when the caller is not in a transaction, falling back to the caller's connection if the observer fails. `_on_connection` stamps `(PRAGMA data_version, total_changes, kinds)` **before** reading and returns a `deepcopy` of the memo when the stamp matches, so callers cannot mutate cached rows and a commit racing the read is seen next call. In a transaction it reads the caller directly (uncommitted writes are visible, nothing is cached). `_read_candidates` decodes `delivery_metadata` as `list_notify_subs` does. In-flight rows are always included (conservative superset). `sweep_once` calls `_clear_drained_stalls(slug, subs)` both when a board has zero TUI subscriptions and after each candidate list.

### 2037: `tui_gateway/notice_candidates.py`

`rows(db, sql)` runs inside `db._read_retrying_ioerr(probe)`; if the store lacks that helper it falls back to `db._read_all(sql)` uncached. The key is `(db, conn, sql)`, the stamp `(data_version, total_changes)`. In-transaction reads (including pinned snapshots) evict and bypass. The cache is an LRU bounded to 32 entries.

### 2038: `tui_gateway/remote_sweep_projection.py`

`_sweep_snapshot` keeps its whole-file fingerprint memo, and on a miss now opens the read-only replica, starts a `BEGIN` snapshot and calls `snapshot(conn, file_identity(stamp), me)`. That reads `(gw, key, seq)` for every `coll='tasks'` row not owned by this gateway, reuses the stored projection where `seq` matches, and fetches `data` only for changed rows. A cold pass reads all payloads in one query. An unknown file identity or a legacy replica without a `seq` column projects everything and retains nothing. Only one `(file identity, me)` scope is retained. `_reset_for_tests` also resets it.

### 2039: `hermes_cli/kanban_sync_repair_content.py`

`run(live, replica, gateway, path, inputs, rebuilt)` ensures the replica's repair-revision trigger counter (outside a transaction), then builds a stamp from each side's `_revision`: connection object, file name, `st_dev/st_ino/st_ctime_ns/st_mtime_ns/st_size`, and the `kanban_repair_revision` counter with `schema_version`. It adds `inputs[2:]` (mode, gateway, placement config; the cursor-bearing replica identity element is excluded). An identical stamp, with `rebuilt` false, returns `{}` without scanning. Otherwise both scans run, and the **pre-scan** stamp is stored only after both succeed, so the scans' own writes and concurrent commits re-arm the next pass. An LRU of 32 boards is kept. `reset_board_watch()` clears it.

## Code

- 2035: `tui_gateway/kanban_unloaded.py` (`sweep_once`, `_clear_drained_stalls`), new `tui_gateway/kanban_unloaded_candidates.py`.
- 2037: `tui_gateway/notice_guarantee.py` (`_CANDIDATES_SQL`, `candidates`), new `tui_gateway/notice_candidates.py`.
- 2038: `tui_gateway/kanban_remote_notices.py` (`_sweep_snapshot`, `_reset_for_tests`), new `tui_gateway/remote_sweep_projection.py`.
- 2039: `hermes_cli/kanban_sync_client.py` (`_sync_board_locked`, `reset_board_watch`), new `hermes_cli/kanban_sync_repair_content.py`.

## Tests

Listed from the patches. Not rerun in this mirror pass.

- 2035, `tests/tui_gateway/test_kanban_unloaded_idle.py` (new): `test_unchanged_sweep_skips_subscription_and_event_scans`, `test_new_event_is_candidate_on_next_sweep_and_retried`, `test_warm_observer_skips_candidate_query_across_reopened_connections`, `test_transaction_and_observer_failure_use_caller`, `test_transaction_reads_uncommitted_caller_with_available_observer`, `test_cursor_and_inflight_commits_invalidate_warm_memo`, `test_warm_claim_recovers_when_holder_dies`, `test_drained_and_deleted_stalls_clear_without_affecting_other_boards`, `test_candidate_rows_match_original_projection_and_are_independent`, `test_kind_key_and_same_connection_writes_are_visible`, `test_commit_during_scan_is_not_cached_under_new_version`, `test_replaced_database_does_not_reuse_old_candidates`.
- 2037, `tests/tui_gateway/test_notice_guarantee_idle.py` (new): `test_unchanged_notice_candidates_do_not_rescan`, `test_aging_without_writes_does_not_need_a_rescan`, `test_external_commit_invalidates_next_read`, `test_same_connection_writes_and_rollback_are_not_cached`, `test_no_trusted_recency_is_excluded`, `test_real_pinned_snapshot_bypasses_warm_cache`, `test_real_writer_fallback_observes_own_commits`, `test_raw_reader_fallback_and_failed_query_retry`, `test_cache_is_bounded_and_reopen_and_sql_are_distinct`, `test_full_pass_uses_retained_launch_profile_reader`, `test_text_notice_timestamp_is_ineligible`.
- 2038, `tests/tui_gateway/test_remote_sweep_projection.py` (new): `test_unrelated_commit_skips_task_projection`, `test_only_changed_task_is_projected`, `test_replacement_gateway_and_deletion_invalidate`, `test_unknown_fingerprint_never_reuses_row_projection`, `test_canonical_row_add_and_remove`, `test_warm_payload_read_is_one_snapshot`, `test_legacy_no_seq_rereads_payload`. `tests/tui_gateway/test_idle_watcher_gates.py` now counts `_ro` snapshot opens instead of `_task_rows` calls, and its commit-during-read test uses a WAL replica.
- 2039, `tests/hermes_cli/test_kanban_sync_repair_content.py` (new; fixtures from `test_kanban_sync_repair_window.py`): `test_event_only_feed_skips_content_but_keeps_partition_repairs` (shadow/primary), `test_relevant_changes_rearm` (live/replica × task/link/schema/trigger), `test_uncommitted_then_rollback_never_caches`, `test_outer_scope_changes_rearm`, `test_rebuilt_and_unknown_inputs_always_scan`, `test_revision_error_evicts_and_retries`, `test_second_scan_error_cannot_publish_success`, `test_concurrent_commit_during_scan_is_seen_next_pass`, `test_reset_and_lru_bound_force_rescan`, `test_trigger_reinstallation_observes_the_following_write`, `test_missing_replica_trigger_inside_transaction_preserves_scan`, `test_unknown_live_revision_never_caches`, `test_new_handle_cannot_inherit_old_handle_stamp`, `test_main_file_rewrite_with_same_identity_and_counter_rearms`.

```sh
scripts/run_tests.sh tests/tui_gateway/test_kanban_unloaded_idle.py tests/tui_gateway/test_notice_guarantee_idle.py \
  tests/tui_gateway/test_remote_sweep_projection.py tests/tui_gateway/test_idle_watcher_gates.py \
  tests/hermes_cli/test_kanban_sync_repair_content.py tests/hermes_cli/test_kanban_sync_repair_window.py
```

The commit messages of 2035/2037 also state that a full cadence replay was recorded separately or still pending. This doc does not claim it.

## Applying

The four patches are independent of each other in code but depend on earlier fork modules (`kanban_reader_retention`, `kanban_replica_observer`, `kanban_sync_repair_revision`, the sync repair window). Apply through the snapshot-bound replay in [README.md](README.md).

## Gotchas

- `data_version` sees other connections' commits, and `total_changes` covers this connection's own writes. Both are needed. Reusing a stamp across a different connection object is deliberately impossible (connection identity is part of every key).
- A replaced database file (board rebuild) must not reuse old rows. 2035 relies on the retained reader's generation guard, and 2039 on stat identity plus timestamps. Main-file timestamps over-invalidate at WAL checkpoint, which is intentional.
- 2039 caches only the content scans. The outer `_REPAIRED`/repair-window gate, which does include the feed cursor, still decides whether a repair round runs at all.
- None of these memos records delivery or claim decisions. A memo hit only skips re-reading unchanged rows.

## Plugin?

No. These are internal read paths of core gateway sweepers and the sync client.
