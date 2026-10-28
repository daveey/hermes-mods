# The sidebar re-reads hub.db's task rows only when they change

Patch **0580** (source `512f93867f`). Follows [kanban-replica-decode-memo.md](kanban-replica-decode-memo.md) (0557).

After the chat-closeout fix ([backend-writable-open-guard.md](backend-writable-open-guard.md)), replaying
`GET /api/profiles/sessions/sidebar` every 2 s against the live `~/.hermes` still spent most of its kanban time in the
remote-links projection: `_project_remote_links` rebuilt on nearly every poll and each rebuild re-read all ~6.1k replica
task rows (~23 MB of JSON) from `hub.db`.

## Why it re-read on every poll

1. **Why did the projection rebuild every poll?** `_links_fingerprint` covers every local `kanban.db`/`-wal`, `hub.db`/`-wal`
   and the origin ledger. Board workers write every few seconds, so a 2 s poll almost never hit the 3 s cache.
2. **Why didn't a file-stat key on `hub.db` alone fix it?** `hub.db`'s WAL changes ~45 times a minute (outbox, cursor,
   request ledger), while its `replica` task rows change only when a pull lands (minutes apart when quiet).
3. **Why was each rebuild expensive?** `_read_remote_links` fetched every row's `data` and looped over all of them in
   Python even when 0557's decode memo already held every decoded row; then `superseded` scanned every ledger row for
   each of ~2.4k legacy cards (~500k `supersedes` calls per rebuild).

## Behavior

- A live replica read (`_read_remote_links(path)`, no explicit `now`) returns the previous answer, with the same
  `qualified_out` / `owners_out` additions, while all of these are unchanged: the `replica` task rows' probe
  (`count`, `count(seq)`, `max(seq)`, `total(seq)`, `total(version)`, `total(octet_length(data))`, index-only, ~6 ms on
  the live replica), the gateway identity, the `gateway/info` heartbeat rows, `last_ok`, `offline_since`, the replica
  schema and the origin profile. hermes-hub stamps every replica write (`pull`, `snapshot`) with the hub feed's
  monotonic `seq`, so any insert or update raises `max(seq)` and a delete lowers `count`.
- Reuse also stops at the next wall time a time cut-off could flip the answer: `last_ok + HUB_STALE_AFTER_S` (hub
  sync goes stale) and, for each scope heartbeat, `beat` and `beat + SCOPE_HEARTBEAT_FRESH_S` (owner release
  eligibility).
- The probe and the rows are read in one transaction, so the memo key always describes the rows it maps to.
- Not memoized: an explicit `now` (always reads every row), a replica without `seq`/`version` columns, or any row
  with a NULL `seq`. A failed read is never cached.
- `_project_remote_links` indexes the receipt rows by the identity each test needs (`supersedes`: owner gateway +
  origin gateway/profile/session; the alias test: key + origin gateway/profile) instead of scanning every row per
  legacy card. Same boolean per card.

Measured on the live `~/.hermes` (30 sidebar polls, 2 s apart, per-function CPU): remote projection 49 to 25 ms per
poll, replica read 34 to 13 ms per poll; the memo served 34 of 79 one-second reads. A 79-read live replay compared
every memoized read with an explicit-`now` read at the same instant: identical answers and out-sets every time.

The whole sidebar poll is still above 0.1 s CPU (154 vs 173 ms per poll in the same replay): the rest is local board
reads (~30 ms), chat-closeout's waiting source (~25 ms) and the state.db session list. Back-to-back projections on
the unmodified tree also differ ~1 in 5 times (`stale`/`status` on legacy ghost rows), independent of this change.

## Code

`hermes_cli/kanban_session_activity.py`: `_read_remote_links` (memo, `_REPLICA_PROBE`), `_project_replica` (the
old body plus `horizon_out`), `_project_remote_links` (`by_origin` / `by_key`), `_reset_cache_for_tests`.

## Tests

`tests/hermes_cli/test_kanban_replica_read_memo.py`: `test_unchanged_rows_are_read_once_and_answer_the_same` (outbox
and cursor writes do not re-read; five reads, zero row fetches), `test_any_task_row_change_rereads` (update, insert,
delete), `test_hub_staleness_cutoff_is_never_served_from_the_memo`, `test_meta_and_heartbeat_changes_reread`,
`test_heartbeat_freshness_bounds_the_reuse_window`, `test_a_replica_without_seq_is_read_every_time`,
`test_origin_profile_is_part_of_the_key`, `test_extending_the_returned_list_never_reaches_the_memo`. The
`superseded` index is covered by the existing legacy/alias suites (`test_kanban_origin_legacy*.py`,
`test_kanban_origin_alias_release.py`), unchanged and passing.

## Applying

`git am -3`; needs 0557 (decode memo) and the origin-ledger/legacy series.

## Gotchas

- The key leans on hermes-hub's invariant that every replica write carries a new feed `seq`. A new writer of
  `replica` that keeps `seq` while changing `data` must bump it, or this memo would serve the old rows (the
  `octet_length` total only catches size changes).
- Cards are shared, read-only, like `_links_cache`; callers may extend the returned list, not edit its dicts.
