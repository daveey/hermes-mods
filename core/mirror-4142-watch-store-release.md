# Watcher release policy and reactivated owners

Mirror snapshot 4142; ordinals 4068, 4069, 4070.

## Behavior

- After the session store is released (all roots, or one root), the gateway change watcher no longer keeps a connection to a released database. The release is remembered even if the store was already evicted from the watcher's 32-entry connection cache.
- For a released path with no tracked owner, the watcher reads session content through a transient connection and compares semantic content (a digest or revision), not per-connection counters. Same-mtime commits, equal-mtime restores and WAL commits without a stat change are therefore seen on the next pass.
- When a tracked owner reopens a released database, only that path becomes watchable again; other released paths stay released. A later release under that root clears the reactivation.
- A database without a readable sessions table, in the transient case, is fingerprinted from a full serialized image, within the caller's snapshot and throttle, so a missing-table database does not broadcast on every throttle interval.
- 4070 changes only a test: the in-place restore test now covers a restore after release.

## How it works

- 4068: `watch_store` replaces the `_released` path set with `_released_roots`, a `_release_all` flag and a `_reactivated` path set. `release_under(root)` records the root (collapsing nested roots) or sets release-all, clears affected reactivations, and drops held connections. A new `_transient(path)` is true when the path is under a released root and no live tracked connection exists; if one exists the path is added to `_reactivated`. `observed()` yields the sentinel `TRANSIENT` as its key in that case and skips the `data_version` read. In `change_watcher`, `_db_content_sig` and `_session_db_content_sig` do not reuse a cached signature when the key is `TRANSIENT`, and `_session_digest` hashes `conn.serialize()` only for the transient foreign-schema fallback.
- 4069: `_transient` checks `_reactivated` first, so a reactivated owner skips the released-root path resolution. The patch text reports a two-root microbenchmark, not repeated here.
- 4070: the restore test is parametrized on releasing tracked readers before the copy.

## Code

Source paths touched by this feature group (including regression fixtures):

- `tests/tui_gateway/test_watch_store_gate.py`
- `tests/tui_gateway/test_watch_store_release.py`
- `tui_gateway/change_watcher.py`
- `tui_gateway/watch_store.py`

Mail entries in the ordered series:

- 4068: [`ce8e71237a940f4b1dae20959ef16de17ba929c9`](patches/4068-daveey-Preserve-watcher-release-policy-and-transient.patch)
- 4069: [`5f2cda3eedaf967b84ad0463d68eba3045c0a769`](patches/4069-daveey-Bypass-released-root-scans-for-reactivated-wa.patch)
- 4070: [`5e5a48cd3ae6a455e1ef75584eec63ca56074ad4`](patches/4070-daveey-Make-watcher-restore-invalidation-fixtures-ex.patch)

## Tests

`tests/tui_gateway/test_watch_store_release.py` (new in 4068, one test added in 4069):

- `test_release_before_observation_and_reset` (all roots or one root): released paths keep no live connection across repeated observation; other roots are unaffected unless all roots were released; `reset` clears the release.
- `test_release_covers_evicted_stores_and_reactivation_is_per_path`: release covers a store evicted from the cache; a tracked owner reactivates only its own path; a later release removes that.
- `test_transient_content_stable_and_same_mtime_commit_seen`, `test_transient_restore_equal_mtime`: content signatures are stable, and same-mtime commits and restores change them.
- `test_transient_missing_table_does_not_broadcast_each_throttle`, `test_transient_due_trailing_edge`, `test_missing_table_fingerprint_is_throttled_and_snapshot_bound`: throttle behavior and snapshot binding of the fallback fingerprint.
- `test_transient_wal_commit_is_seen_next_pass_without_stat_change`: a WAL commit is seen on the next pass; an untracked writer does not reactivate the path.
- `test_transient_normal_store_never_serializes`: a normal sessions store uses a revision signature and never calls `serialize`.
- `test_transient_replay_cost`: 60 repeated passes return the same signature and record timing as a property; it asserts no timing bound.
- 4069: `test_reactivated_owner_skips_released_root_scans`: a reactivated path does not call `path_is_under`; after another release it is transient again and no longer reactivated.

`tests/tui_gateway/test_watch_store_gate.py` (4070): `test_in_place_restore_reopens_sqlite_page_cache` is parametrized on `release_before_restore`; with release, a same-size, same-mtime restore still yields a different key and the new data.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- Transient reads rely on content, so for a foreign-schema database each throttled pass can serialize the whole file; normal sessions stores avoid this.
- The release applies to the gateway watcher's own connections; an untracked external writer does not reactivate a path.
- Paths are compared after resolving the released root; symlink layouts were not exercised in the tests shown.
- This document makes no deployment claim.

## Plugin?

No. This is internal state in the gateway's watcher and its interaction with the core state registry, which a plugin cannot replace.
