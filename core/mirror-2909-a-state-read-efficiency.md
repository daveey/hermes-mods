# Safe clean-WAL verdict reuse and exact bounded recency pages

Patches: **2748, 2768**.

Related context: [wal-holder-scan-single-flight.md](wal-holder-scan-single-flight.md), [kanban-replica-read-memo.md](kanban-replica-read-memo.md).

## Behavior

A clean deleted-WAL-holder verdict survives unrelated directory churn when both sidecars have strong identities, avoiding repeated whole-process FD walks. Session recency pages preserve exact compression-chain ranking while probing message timestamps only for rows that can reach the page.

## How it works

2748 keys sidecars by device, inode and birth time (Darwin birth timestamps or Linux `statx(STATX_BTIME)`). Directory identity remains in the key; its mtime is needed only with a missing sidecar or unknown birth time. Positive holder results are not cached. Every timestamp must predate a two-second settle window measured from before the key was taken, not after a potentially slow walk, preventing inode reuse within the same coarse clock tick. 2768 extracts the exact ranking query into `hermes_state_recency.py`. A per-file-identity, bounded eight-store cache tracks in-window message maxima and an AUTOINCREMENT message-ID watermark. New messages refresh only their sessions; deletion/rewind leaves a safe upper bound. A threshold from the (offset+limit)-th admitted activity timestamp yields indexed candidates plus their ancestors; the exact SQL query then ranks that superset in the same snapshot. High stale bounds are reprobed. Searches, insufficient threshold rows, missing reliable IDs, invalid paging or SQLite errors use the unrestricted query; replaced stores start over.

## Code

- `hermes_state_dbfile.py`
- `hermes_state_recency.py`
- `hermes_state_sessions.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_state/conftest.py` (fixture/assertion changes; no test definition name appears in this diff).
- `tests/hermes_state/test_darwin_holder_snapshot.py`: `test_native_scan_does_not_refuse_after_normal_sidecar_close`, `test_descriptor_closing_during_path_comparison_is_not_a_holder`.
- `tests/hermes_state/test_deleted_wal_generation_guard.py`: `test_darwin_clean_verdict_is_dropped_once_a_sidecar_is_unlinked`, `test_linux_clean_holder_scan_is_reused_until_a_sidecar_changes`, `test_linux_clean_holder_scan_survives_unrelated_directory_churn`, `test_linux_clean_holder_scan_rechecks_when_freed_sidecar_numbers_are_reused`, `test_linux_clean_verdict_is_not_cached_while_key_timestamps_share_a_tick`, `test_settle_window_counts_back_from_key_take_not_from_a_slow_walk`, `test_linux_sidecar_birth_time_is_read_and_survives_writes`, `test_linux_positive_holder_scan_is_not_cached`.
- `tests/hermes_state/test_recency_page_probes.py`: `test_polls_after_one_insert_do_not_probe_every_session`, `test_pages_match_the_unrestricted_ranking_through_writes`, `test_deleted_messages_leave_no_stale_rank`, `test_replaced_store_starts_over`.
- `tests/hermes_state/test_state_db_darwin_fd_scan_hang.py`: `test_clean_verdict_is_reused_and_scans_are_single_flight`, `test_caller_arriving_after_pass_but_before_cache_write_reuses_it`.

Coverage is limited to the entry points and fixture controls above; live services, deployment and user-interface behavior are not established by this documentation task.

## Applying

Use the [README full replay procedure](README.md#applying). Apply these patches in published ordinal order with the intervening series, replay bridges and mandatory post-replay repair described there. They are incremental changes, not independently certified cherry-picks. This document does not claim that the full mirror replay or feature suites ran during the documentation pass.

## Gotchas

Missing birth time deliberately retains directory-mtime invalidation. Fresh/future timestamps remain unsettled; a slow scan does not make them safe. Recency optimization assumes no live in-place upward edits or session reassignment of existing message timestamps and no explicit old-ID insertion; a repaired replacement file receives a new cache identity. Tests compare unrestricted rankings across writes/deletes, but this pass did not perform native FD walks or live sidebar performance measurements.

## Plugin status

Core/CI implementation; no plugin is added or required. A plugin cannot replace the shared database, admission, worker or workflow invariants documented here.
