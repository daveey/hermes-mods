# Guarded replica readers, bystander evidence and reader caches

Mirror snapshot 4142; ordinals 4071, 4104, 4129, 4130, 4131.

## Behavior

- Dispatcher operator-call scans and awaited-card wake scans reuse a bounded, generation-aware cached replica reader instead of opening a new guarded read-only connection on each tick. Results are never cached: a commit made outside the process is visible on the next borrow.
- A borrowed reader returns rows as `sqlite3.Row` with a 5 s busy timeout. When the borrow ends, on success or on any exception, the open transaction is rolled back and the shared reader's row factory and 500 ms busy timeout are restored. An error raised in the caller's body propagates; only errors while acquiring the reader turn into "no replica".
- If the shared reader is already held (a nested borrow, or another thread), the caller does not wait. It falls back to a private transient read-only connection that is closed on exit.
- A missing replica file is reported as a miss and is not created. The replica path is resolved on every borrow.
- The session-activity sidebar read keeps, for replica task rows that do not concern the local gateway ("bystanders"), only a small marshalled tuple of scalars (seq, size, version, updated time, created time, a 16-byte text digest, and terminal/verified flags). Task payload text is retained only for rows that are relevant to the local gateway.
- Closed/owner/publication aggregates for bystanders are answered from those scalars through lazy set and mapping views. The legacy remote-link path borrows the closed set and resolves all remembered publications in one scan.
- A remembered card from a previous read only participates in the publication lookup when it is qualified, has a hashable non-null `created_at`, and belongs to a scope that is currently live. Ineligible remembered cards (for example `created_at` of `[]` or `{}`, or an unknown ownership state) no longer break later reads.
- Reader-cache eviction callbacks registered with the generation module are held weakly, so retired plugin cache instances and their SQLite handles can be garbage-collected after a reload. A new `unregister_reader_cache` removes one callback and returns `True`, or `False` if it was not registered.
- An unfiltered structural replica view (one without the scalar projection fields) is still accepted by the projection code.

## How it works

- 4071: New module `hermes_cli/kanban_replica_reader.py` provides `open_replica(db_path)`. `_borrow` takes the cached entry for the path, tries a non-blocking acquire of the entry lock, checks the entry is still in the cache (else raises a deleted-generation error), sets `Row` and the 5 s timeout, and on exit rolls back and restores defaults. A SQLite error discards the cache entry. `open_replica` retries once on a programming/deleted-generation error, then logs and yields `None`. After the borrow the retention pruner runs. `kanban_operator_call._replica` and the awaited-card wake `_replica_finished` now import `open_replica` from this module; the general `foreign_card.open_replica` (close-on-exit) is unchanged. A replay script, `scripts/kanban_replica_drain_replay.py`, runs the real dispatcher drain with populated replicas, many open descriptors and an optional large WAL, and compares the old and new reader per tick. It fails the run if any network or process spawn is attempted.
- 4104: `_reader_caches` in `kanban_generation.py` becomes a small registry class that stores `WeakMethod` (or `weakref.ref`) entries, with a strong reference only for callables that cannot be weakly referenced. Dead entries are pruned on iteration, length checks and registration. Iteration and membership still yield the live callables. Snapshots are taken under the registry lock so recovery calls caches outside it.
- 4129: A snapshot-scoped reader (`SnapshotRows`) is returned by `kanban_replica_rows.read(..., me=...)` and consumed once. `derive` decodes each row once and, when `is_bystander` (in the new `kanban_replica_relevance.py`) says it is irrelevant to this gateway, stores only scalars; otherwise the packed payload is kept. A row is relevant if its key is malformed, it has origin bindings, it links a session of this gateway, or it is the qualified twin of a legacy two-part key. Delta reads re-derive only changed rows, check totals against the probe, drop removed rows, re-fetch payloads in pages of at most 100 inside the caller's open snapshot when a bystander is promoted by a legacy alias, and reclassify an unchanged qualified twin when its legacy alias disappears. `kanban_replica_transport.iter_page` decodes SQLite's JSON-array pages one row at a time with an incremental decoder, so a page is never widened to one large string. `kanban_replica_aggregates.py` builds `EvidenceSet`, `BorrowedSet` and `Published` views from the scalars; `kanban_replica_memo.finish` publishes the read memo without copying all-row aggregates. The projection layer shares scalar maps per replica path through a `ChainMap`, and unapplies payload stages of other profiles' states for rows that have become bystanders.
- 4130: `_advance` and `project` in `kanban_replica_projection.py` read `me` and `bystanders` with `getattr` defaults, so a view built by the unfiltered path still works.
- 4131: `remembered_publications` applies the same eligibility as the moved-card retirement before building lookup keys, and skips keys that are not hashable.

## Code

Source paths touched by this feature group (including regression fixtures):

- `hermes_cli/kanban_awaited_card_wake.py`
- `hermes_cli/kanban_operator_call.py`
- `hermes_cli/kanban_replica_reader.py`
- `scripts/kanban_replica_drain_replay.py`
- `tests/hermes_cli/test_kanban_operator_retained_replica.py`
- `tests/hermes_cli/test_kanban_replica_reader.py`
- `hermes_cli/kanban_generation.py`
- `tests/hermes_cli/test_kanban_generation_reader_cache_registry.py`
- `hermes_cli/kanban_replica_aggregates.py`
- `hermes_cli/kanban_replica_memo.py`
- `hermes_cli/kanban_replica_projection.py`
- `hermes_cli/kanban_replica_relevance.py`
- `hermes_cli/kanban_replica_rows.py`
- `hermes_cli/kanban_replica_scalar_rows.py`
- `hermes_cli/kanban_replica_transport.py`
- `hermes_cli/kanban_session_activity.py`
- `tests/hermes_cli/test_kanban_replica_bystander_memory.py`

Mail entries in the ordered series:

- 4071: [`d3b42fa007a61dcff264b0159a89f08c1e24aa33`](patches/4071-daveey-Reuse-guarded-replica-readers-in-dispatcher-o.patch)
- 4104: [`15147bd5077ea7f0b6256e417b5e47e7cbf221ff`](patches/4104-fix-kanban-weakly-retain-reader-cache-eviction-callb.patch)
- 4129: [`35904cf2b17f24f3d87d1f7edd71c16b4d2ec9ae`](patches/4129-daveey-retain-scalar-evidence-for-session-replica-by.patch)
- 4130: [`3eb091d0a85a1b897af5aaa2f7288a7bdbcb69fa`](patches/4130-daveey-preserve-unfiltered-structural-replica-view-p.patch)
- 4131: [`80d22d01d9c1a688a9d6739fbd1e1cbb6e4c1e39`](patches/4131-fix-kanban-filter-remembered-publication-lookup-elig.patch)

## Tests

`tests/hermes_cli/test_kanban_replica_reader.py` (4071):
- `test_reuses_guarded_open_and_observes_external_commit`: two borrows share one guarded open and the second sees an external commit.
- `test_transaction_release_and_error_propagation`: with no error, `ValueError` and `sqlite3.OperationalError` in the body, the transaction is released and the next borrow sees fresh data; body errors propagate.
- `test_row_readonly_timeout_and_shared_reset`: `Row` factory, 5000 ms timeout, writes rejected; the shared reader afterwards has no row factory and a 500 ms timeout.
- `test_nested_and_concurrent_borrow_do_not_wait_on_shared_lock`: nested and cross-thread borrows get a different connection and still read.
- `test_bound_and_active_borrower_retirement`: with the retention limit at 0, an active borrower keeps working and the entry is evicted and closed afterwards.
- `test_path_resolved_each_borrow_and_missing_not_created`: a missing path yields `None` and no file; a later-created file is read.
- `test_replacement_reopens_without_manual_cache_clear`, `test_deleted_sidecars_recover_before_reuse`, `test_closed_cached_handle_recovers`: replaced files, deleted WAL/SHM files and a closed handle all reopen.
- `test_evicted_entry_between_prepare_and_lock_is_a_miss`: an entry evicted before the lock yields `None`, then recovers.

`tests/hermes_cli/test_kanban_operator_retained_replica.py` (4071): `test_real_reader_operator_contract` runs six existing operator-call cases (foreign-card comment, no double replay, unreachable board defers, home executes only the named card, result waits when the board cannot vouch, rebuilt replica rescanned at a reused position) through the real retained reader.

`tests/hermes_cli/test_kanban_generation_reader_cache_registry.py` (4104):
- `test_retired_caches_release_five_readonly_connections`: after dropping five cache instances and collecting, the registry is empty and their connections are freed.
- `test_unregister_removes_only_matching_entry_and_is_idempotent`: duplicate registration, single removal, second removal returns `False`.
- `test_recover_calls_live_cache_outside_registry_lock`: `recover` calls a live cache's evict while another thread can take the registry lock.
- `test_function_lifetime_and_registration_prune_dead_entries`: a dead function entry is pruned on the next registration.
- `test_non_weakrefable_callable_is_retained_until_unregistered`: a slotted callable is held strongly until unregistered.

`tests/hermes_cli/test_kanban_replica_bystander_memory.py` (4129, 4131):
- `test_bystander_definition_is_gateway_wide_and_conservative`: relevance rules for sessions, origin bindings, legacy keys and malformed keys.
- `test_payload_refetch_is_paged_parameterized_and_uses_callers_snapshot`: refetch pages of 2 give 3 SELECTs, see the snapshot's original data, keep the transaction open, and reject a bad column name or page size.
- `test_page_decoder_streams_one_row_without_widening_whole_page` (utf-8, utf-16le, utf-16be), `test_refetch_transport_has_one_page_peak`, `test_cold_row_transport_has_one_page_peak`: traced memory peaks stay bounded by about one page.
- `test_snapshot_reader_retains_only_scalars_for_bystanders`: 2000 bystanders keep empty payload slots, retained memory stays under 400 bytes each plus slack, and a second `consume` raises.
- `test_live_projection_bystander_retention_is_below_400_bytes_each`: same bound through the live read, no payload text reachable from the module caches.
- `test_unfiltered_read_after_filtered_snapshot_restores_real_payloads`, `test_filtered_rows_raw_access_is_explicitly_rejected`.
- `test_alias_insert_and_delete_reclassify_unchanged_seq`: inserting or deleting a legacy alias moves a row between bystander and relevant with identical results to a full read.
- `test_scalar_closed_membership_does_not_stringify_identity_parts`.
- `test_unchanged_relevant_rows_are_not_decoded_again`, `test_last_link_demotion_releases_all_profiles_payloads`.
- `test_legacy_reader_borrows_closed_and_bulk_resolves_publications`: the remembered-card path does not iterate all-row aggregates.
- `test_remembered_bystander_retirement_matches_full_reader` (closed, moved, same-gateway-profile): the scalar path matches the full reader.
- `test_ineligible_remembered_publication_does_not_poison_reads` (4131): ownership `unknown` with `created_at` `[]` or `{}`, and `qualified` with `[]`, leave the next two reads unaffected.

4130 adds no test of its own. The `scripts/kanban_replica_drain_replay.py` script is a manual measurement tool, not a test.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- The new reader is only for scopes that consume all results before leaving; callers must not keep the connection or a cursor. Other callers keep the close-on-exit behavior.
- Under contention the fallback opens an independent connection, so the saving applies only to uncontended borrows.
- The replay script uses synthetic idle workloads. The commit message reports a per-tick cost drop; it is not a whole-system acceptance result and this document makes no deployment claim.
- Marshalled scalars are generated in-process and never loaded from a database. A filtered view cannot return raw rows (`rows()` raises); raw payloads exist only inside the unconsumed snapshot.
- Relevance is deliberately conservative: any link to the local gateway, any origin binding, or a legacy twin keeps the full payload.
- 4130 and 4131 have the narrowest coverage: 4130 has no dedicated test, and 4131's test covers the three listed ineligible shapes.
- The operator-call path is exercised by the retained-replica test; the awaited-card wake path has no test in these patches (untested).

## Plugin?

No. These change core SQLite reader ownership, cache registries and the replica projection internals, which a plugin cannot reach.
