# One sidebar poll reads each store once

Patch **0656** (source `2bc039b9bf`). Follows [kanban-replica-read-memo.md](kanban-replica-read-memo.md) (0592) and
[sessions-list-preview-page-only.md](sessions-list-preview-page-only.md) (0533).

After 0592, replaying `GET /api/profiles/sessions/sidebar` every 2 s against the live `~/.hermes` still cost
~170 ms of backend CPU per poll (Desktop's own query: ~85–115 ms). The response cache (5 s) lets about one poll in
three through; each of those spent ~600 ms.

## Why each uncached poll cost ~600 ms

1. **Why were kanban counts recomputed ~2.3 times per response?** The handler annotates its recents, cron and
   messaging slices in turn, each through `annotate_kanban_activity` → `_all_counts`. The counts cache key covers
   every board's db/wal and `hub.db`; a write between two slices (hub.db alone is written ~45×/min) made the next
   slice recompute everything (~130 ms).
2. **Why was one recompute ~60 ms of local board reads?** Each board was opened five times (unseen, active,
   blocked, parked, stall rows), ~1 ms per open, ~6 ms per board even with no rows; the 7 boards were all re-read
   on every recompute.
3. **Why didn't a per-board file-stat memo hit?** Every board's `-wal` mtime moves every ~6 s with no content change
   (a periodic writer rewrites the WAL in place), so at a 5 s cadence most boards look changed. Hence the shared
   connection (fix 2) does most of the work; the memo still skips boards that did not move.
4. **Why ~150 ms of state.db per response?** Three `list_sessions_rich` calls; each evaluated the session filter
   (three `json_extract`s on `model_config` per row, ~8 ms) twice and every session's `MAX(messages.timestamp)`
   (~10 ms) twice: once in the `chain_max` CTE and again for each admitted row before `ORDER BY … LIMIT`.
5. **Why did the replica read memo (0592) almost never hit?** Its key held `last_ok`, which every hub pull (~2 s)
   moves; 24 of 28 live reads missed on `last_ok` alone with the task rows unchanged.
6. **Why ~50 ms per remote projection beyond the replica read?** It resolved each receipt alias's compression tip
   on a fresh connection to the 12 GB state.db (~24 opens per projection), and a board WAL moving between the
   counts and chat-closeout's pass made the same response project twice.
7. **Why ~14 ms per slice for the pinned back-fill?** Planned as a walk of every session in `started_at` order
   with the full filter on each row, for a handful of pins.
8. **Why ~80 ms per chat-closeout pass?** Each of ~50 per-card transcript proofs opened a `SessionDB` (for the
   compression tip) and a fresh connection per state.db, and the pass projected the origin ledger again
   (`project()`, which also takes the ledger write lock) although core had just projected it.

## Behavior

- `kanban_session_activity.one_annotation_snapshot()` (context manager, per thread, nestable): every
  `annotate_kanban_activity` call inside reads the counts, parked/held/stalled maps, the plugin waiting sources and
  the attention counts once, on the first call; the rest reuse that read. A failed read is not shared: the next
  call in the block reads again. `GET /api/profiles/sessions/sidebar` wraps its three slices in one.
- Local board reads: one shared read-only connection (`cached_ro`) and one read transaction per board for all five
  reads (one snapshot per board, where there were five). Memoized per board on its db and `-wal` stat
  (inode, size, mtime_ns): a board that has not moved is not read. Only the stall verdict depends on the clock, so
  the memo keeps the stored rows and re-judges them on every use (`_stalled_counts(..., now)`). A board whose
  unseen or stall read failed (logged, counted as nothing, as before) is not memoized.
- `list_sessions_rich(order_by_last_active=True)`: the page is picked from `chain_max` (its roots are exactly the
  rows the filter admitted, so the filter runs once) by `(effective_last_active, started_at, id)`; columns,
  `last_active` and the preview are projected onto the picked page only. Same rows, same order, same columns.
- Pinned back-fill: driven from `SELECT id FROM sessions WHERE pinned = 1`, then the same filter and columns.
- Replica read memo (0592): the key drops `last_ok`; a hit also requires the same stale verdict and the same
  per-owner `synced_at` (`_replica_synced`, the projection's own expression over the memoized rows' update times),
  which are the only ways `last_ok` reaches the answer. Reuse still ends at the next scope-heartbeat cut-off.
- `_project_remote_links` resolves tips on one state.db connection per projection
  (`kanban_origin_adapter._resolve_session(path, sid, conn)`, each lookup in its own read transaction, ended by a
  rollback). Inside `one_annotation_snapshot`, every `_remote_links` / `remote_links_and_origin_rows` reader shares
  the first projection.
- `remote_links_and_origin_rows()`: `(_remote_links(), origin rows)` from one cached projection; the origin rows are
  the ledger `project()` rows (released rows included) the cards were built from.
- chat-closeout (plugin, hermes-mods): `waiting_source` runs its pass inside `_proof_pass()`: one connection per
  state.db (`core.ReadPool`), one read-only `SessionDB`, one compression-tip lookup per chat; `remote_cards` takes the
  origin rows from `remote_links_and_origin_rows` when core has it.

## Measured

Live `~/.hermes`, 30 polls 2 s apart, process CPU per poll, interleaved runs of `main` (529b6c9f66, with 0592) and
this change on an otherwise idle machine:

| request | main | this change |
|---|---|---|
| harness (`/api/profiles/sessions/sidebar`, defaults) | 167–176 ms | 79–86 ms |
| Desktop's query (excludes, `recents_profile=default`, 50/50/100) | 85–116 ms | 56–63 ms |

Per poll after the change (defaults): state.db lists ~35–45 ms, kanban counts ~20–25 (remote projection ~11–16,
local boards ~2–7), chat-closeout ~10, the rest request handling. Live equivalence: 43/43 memoized replica reads
equal a forced full read at the same instant (memo hits 35/43, from ~1/28); ten `list_sessions_rich` parameter sets
give identical page and pinned rows, old and new SQL, in one read transaction.

## Code

- `hermes_cli/kanban_session_activity.py`: `one_annotation_snapshot`, `annotate_kanban_activity`;
  `_read_board`, `_board_stat`, `_board_memo`, `_read_local_boards`; `_counts_on`, `_parked_on`, `_unseen_on`,
  `_stall_rows_on`, `_local_wait_rows`, `_describe_waits`, `_stalled_counts` (the old path-taking readers are thin
  wrappers); `remote_links_and_origin_rows`, `_links_entry`, `_store_links`, `_origin_rows_sink`.
- `hermes_cli/web_routers/profiles.py`: `get_profiles_sessions_sidebar` wraps its slices in `one_annotation_snapshot`.
- `hermes_state_sessions.py`: `list_sessions_rich` (the `pick` subquery, the pin-driven back-fill).
- `hermes_cli/kanban_session_activity.py` (remote): `_read_remote_links` (key without `last_ok`), `_replica_synced`,
  `_project_replica(seen_out=)`, `_project_remote_links` (shared tip connection), `_links_entry` / `_links_lookup`
  (in-scope pin). `hermes_cli/kanban_origin_adapter.py`: `_resolve_session(conn=)`.
- Plugin: `plugins/chat-closeout/__init__.py` (`_proof_pass`, `state_dbs`, `session_proof`, `_compression_tip`,
  `remote_cards`), `plugins/chat-closeout/core.py` (`ReadPool`, `ack_proven(pool=)`).

## Tests

`tests/hermes_cli/test_kanban_sidebar_poll_reads.py`:
`test_unchanged_boards_are_not_read_again_and_give_the_same_counts` (zero board reads on the second pass; same
counts as a memo-less read), `test_a_write_rereads_only_the_board_that_moved`,
`test_a_memoized_board_still_turns_stalled_when_the_clock_passes_the_cutoff`, `test_a_failed_display_read_is_not_memoized`,
`test_annotations_in_one_scope_share_one_read` (3 slices: 1 read of counts, waiting sources and attention instead of
3; same rows), `test_a_failed_read_in_a_scope_is_retried_by_the_next_annotation`,
`test_remote_links_and_origin_rows_share_one_projection`,
`test_session_tips_resolve_the_same_on_one_shared_connection`, `test_one_scope_projects_the_remote_links_once`.
`tests/hermes_cli/test_kanban_replica_read_memo.py`: `test_a_new_hub_pull_alone_serves_the_memo` (three pulls, zero
row reads, each equal to a forced read), `test_a_pull_that_moves_a_cards_synced_at_rereads`.
`tests/hermes_cli/test_profiles_sidebar_scope.py`: `test_sidebar_slices_share_one_kanban_read` (REST: one count read
and one waiting-source read for the three slices, each row stamped as an unscoped annotation stamps it).
The `list_sessions_rich` rewrite is covered by the existing ordering/search/pin/lineage suites (`tests/hermes_state`,
the sidebar and session-list tests; same pass/fail set as the base tree), plus a live check: ten parameter sets
(recents, cron, messaging excludes, offset, search, id query, subagents, archived, full rows, min messages) run old
and new SQL in one read transaction on the live 12 GB state.db: identical rows every time, 35–50% less CPU.
Plugin: `plugins/chat-closeout/tests/test_proof_pass.py` (`test_a_pass_proves_the_same_with_one_open_of_each`,
`test_the_waiting_source_runs_its_pass_inside_one_proof_pass`,
`test_remote_cards_take_the_origin_rows_from_cores_cached_projection`).

## Applying

`git am -3`; needs 0592 (replica read memo) and 0533 (page-only preview).

## Gotchas

- The board memo trusts (inode, size, mtime_ns) of `kanban.db` and `-wal`, like `_activity_fingerprint`. A writer
  that changes rows without moving either file would be missed (SQLite always does).
- `one_annotation_snapshot` is per thread: the handler's slices run on one worker thread. Don't spread one
  snapshot's annotations across threads.
- Builds on fa44287eb6 (links fingerprint keyed on hub content, 15 s max age), which landed meanwhile: its
  fingerprint still covers every board's stat, which is why the in-scope pin above matters.
- The `stale`/`status` flicker on origin rows under ledger write-lock contention is fixed separately in 0664
  ([kanban-local-owner-projection.md](kanban-local-owner-projection.md)).
