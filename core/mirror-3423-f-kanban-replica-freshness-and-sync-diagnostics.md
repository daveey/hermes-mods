# Kanban replica freshness, ready-wait notices and sync diagnostics

Patches: **3270, 3273, 3282–3284**.

## Behavior
- "Ready with no worker" chat-wait notices check the live board against its pulled replica: when the live feed lags the replica by more than 2000 rows or the replica pull is older than 600 s the notice is deferred; a card the replica shows completed later skips the notice; an in-sync replica still posts once.
- Freshness compares only primary-mode boards this home syncs (shadow, detached or disabled boards are not compared), and a card with a pending outbox op keeps its live row rather than a replica row that predates the local change. Fail-open: no replica or an unreadable one changes nothing.
- Chat-link backfill reads provenance verdicts before its write transaction, so a pass outliving the source cache no longer reopens state databases per row with the board write lock held; a link arriving mid-pass owned by another gateway is classified without a read, and a local one fails closed and is retried next pass.
- The per-board chat-link log line now ends `in <s>s`, and a no-change pass that takes 10 s or more still logs.
- Test fakes: the fake hub/service release routes share one diagnostic/evidence helper (diagnostic truncation like JSON stringify, neutral merge of extras, rejection of non-object diagnostics).

## How it works
- 3283 adds `kanban_replica_freshness` (`lag_hold`, `newer_rows`) used by `kanban_chat_wait` and `kanban_wake_fold`; 3284 gates it on `board_mode == "primary"` and skips cards with pending outbox ops.
- 3282 prefetches machine-session verdicts (`LinkVerdicts`) and skips other gateways' refs. 3270 times the pass in the sync client. 3273 refactors the fake board.

## Code
- `hermes_cli/kanban_replica_freshness.py`
- `hermes_cli/kanban_chat_wait.py`
- `gateway/kanban_wake_fold.py`
- `hermes_cli/kanban_creator_chats.py`
- `hermes_cli/kanban_session_provenance.py`
- `hermes_cli/kanban_sync_client.py`
- `tests/hermes_cli/kanban_sync_fake_board.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_kanban_wake_fold.py`: `test_the_relabel_reads_the_fresher_replica_status`.
- `tests/hermes_cli/test_kanban_ready_wait_checkpoint.py`: `test_replica_completed_later_skips_the_notice`, `test_live_feed_lag_defers_the_notice_until_it_closes`, `test_stale_replica_pull_defers_the_notice`, `test_in_sync_replica_still_ready_posts_once`, `test_shadow_mode_board_is_not_compared`, `test_detached_or_disabled_board_is_not_compared`.
- `tests/hermes_cli/test_kanban_chat_links_bulk_provenance.py`: `test_backfill_reads_no_provenance_inside_its_transaction_when_the_pass_outlives_the_cache`, `test_backfill_with_expiring_cache_matches_the_per_row_implementation`, `test_link_verdicts_never_read_for_a_link_missing_from_the_map`, `test_link_verdicts_match_per_row_is_machine_session`, `test_prefetch_skips_other_gateways_refs`, `test_prefetch_leaves_scoped_verdicts_unchanged`.
- `tests/hermes_cli/test_kanban_sync_steady_links.py`: `test_the_links_log_line_carries_the_pass_duration`.
- `tests/hermes_cli/test_kanban_sync_rejections.py`: `test_fake_release_truncates_diagnostic_like_json_stringify`, `test_fake_release_neutral_merges_extra_on_both_routes`, `test_fake_release_rejects_non_object_diagnostic`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A local chat link that arrives mid-backfill is deliberately left unresolved until the next pass.
