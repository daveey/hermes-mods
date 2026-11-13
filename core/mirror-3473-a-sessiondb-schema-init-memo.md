# SessionDB: memoized schema init per settled store

Patches: **3440**.

## Behavior
- Opening a writable `SessionDB` handle on a store already initialized in this process skips the schema SQL, column reconcile, key heals and the full-table delegate-marker heal scan, which profiling showed as a notable share of a steady serve's time.
- The memo is invalidated by a replaced file (new inode), DDL by another process, a changed data-migration level, `fts_stale` set by another handle, or a different connection class. FTS probe failures fall back to the full init.
- The delegate-marker heal runs with the full init, otherwise at most once per 600 s per store; a sqlite error is logged and retried next interval.

## How it works
3440 adds `hermes_state_schema_memo.py`: a process-level memo keyed on realpath, device/inode, schema versions, a digest of the schema/migration sources and the connection class. `_init_schema` wraps the old body (`_init_schema_full`). A full init is recorded only if the store is settled. On a hit, scalar attributes the first init set are restored after re-probing both FTS tables.

## Code
- `hermes_state_schema.py`
- `hermes_state_schema_memo.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_state/test_schema_init_memo.py`: `test_a_second_open_of_a_settled_store_skips_the_schema_init`, `test_a_skipped_init_restores_the_flags_the_first_init_set`, `test_a_runtime_without_the_trigram_index_does_not_inherit_the_flag`, `test_a_replaced_file_reruns_the_init`, `test_ddl_by_another_process_reruns_the_init`, `test_a_downgraded_data_version_reruns_the_migrations`, `test_fts_stale_set_by_another_handle_is_seen_on_the_next_open`, `test_the_delegate_marker_heal_runs_at_most_once_per_interval`, `test_the_throttled_heal_repairs_a_polluted_row`.
- `tests/hermes_state/test_reset_row_delegate_marker.py`: two reopen-after-surgery tests now clear the memo first.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Tests that edit a store behind the handle's back and expect the heal on reopen must clear the memo first.
