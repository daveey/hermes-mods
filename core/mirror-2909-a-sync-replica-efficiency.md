# Feed-credited repair windows and bounded replica projection memory

Patches: **2735, 2784, 2785, 2787**.

Related context: [kanban-sync-loop-steady-rounds.md](kanban-sync-loop-steady-rounds.md), [kanban-replica-read-memo.md](kanban-replica-read-memo.md).

## Behavior

A pull consisting only of captured remote feed applies can defer the full repair pass, rather than repairing on every heartbeat written into the primary live file. Replica views retain compressed rows, decode full records only where needed, and read large result sets in pages. Release evidence survives slimming. Attachment upload tests reject a receipt with correct digest and size but the wrong blob key.

## How it works

2735 captures `ApplyResult.revision_before/after` inside each apply transaction. `_defer_repair` accepts an unbroken revision chain from the last completed/credited pass to the current input revision, while generation/schema/mode/gateway/placement/replica identity remain stable. A gap, unknown revision, resync/rebuild or missing table repairs immediately. Completion and `reset_board_watch` clear credit; shared state moves to `kanban_sync_repair_window.py`. 2784 stores zlib level-1 packed text bytes and exposes `text`/unpacked public rows. Full reads page by rowid inside one snapshot, targeting about 2 MiB text with 50–1000 rows based on mean size. Unlinked records retain `SLIM_FIELDS`; linked records stay whole, and legacy aliases rehydrate slim records when links fold into them. 2785 computes terminal attestation/verified release from the whole record before slimming, and sizes deltas first: at most 1000 rows and 2 MiB use one seq-indexed row-fetch query (plus the size probe), larger deltas page. Memory tests use 2000 roughly 20-KB records, measure actual allocations with tracing, and compare exact projection semantics. 2787 adds a foreign `blob_key` to the unverified-upload-receipt matrix; local attachment custody must remain.

## Code

- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_replica_projection.py`
- `hermes_cli/kanban_replica_rows.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_repair_window.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_replica_rows_memory.py`: `test_a_live_read_keeps_packed_slim_rows_and_peaks_below_the_text`, `test_a_full_read_pages_the_rows`, `test_a_slim_row_a_legacy_alias_links_decodes_whole_again`, `test_a_small_delta_is_one_query_and_a_big_one_pages`, `test_a_slim_twin_keeps_the_release_evidence_of_its_whole_record`.
- `tests/hermes_cli/test_kanban_sync_attachments.py`: `test_unverified_upload_receipt_keeps_local_custody`.
- `tests/hermes_cli/test_kanban_sync_feed_apply_credit.py`: `test_a_remote_write_to_an_own_card_defers_the_full_pass`, `test_two_apply_only_rounds_chain_and_a_local_write_breaks_the_chain`, `test_a_cursor_only_apply_does_not_break_the_chain`, `test_a_full_pass_clears_the_credit`, `test_a_push_ack_after_the_inputs_read_breaks_the_chain`, `test_a_heal_that_writes_after_the_inputs_read_breaks_the_chain`, `test_reset_board_watch_forgets_the_credit`, `test_an_uncaptured_write_before_the_pull_repairs_in_that_round`, `test_an_uncaptured_write_between_two_apply_pages_repairs_in_that_round`, `test_an_uncaptured_write_after_the_last_apply_repairs_in_that_round`, `test_an_unknown_apply_revision_gets_no_credit`, `test_an_apply_on_a_board_without_the_revision_table_reports_none`, `test_a_missing_revision_table_repairs_at_once`, `test_applies_chain`.
- `tests/tui_gateway/test_replica_bounded_reads.py`: `test_warm_observation_identity_scan_is_bounded`.

Coverage is limited to the entry points and fixture controls above; live services, deployment and user-interface behavior are not established by this documentation task.

## Applying

Use the [README full replay procedure](README.md#applying). Apply these patches in published ordinal order with the intervening series, replay bridges and mandatory post-replay repair described there. They are incremental changes, not independently certified cherry-picks. This document does not claim that the full mirror replay or feature suites ran during the documentation pass.

## Gotchas

The repair interval is still the existing `REPLICA_REPAIR_MIN_S`; credit cannot hide local writes before, between or after applies, push ACKs or heal writes. Page byte sizes are targets based on means, not a hard bound on an individual huge row. Slimming must never discard hash-bound release evidence before it is evaluated. Compression reduces retained heap but still costs encode/decode CPU. No live memory benchmark, Board sync, attachment upload or feature test was executed in this mirror documentation pass.

## Plugin status

Core/CI implementation; no plugin is added or required. A plugin cannot replace the shared database, admission, worker or workflow invariants documented here.
