# Profile resource custody and retirement before deletion

Patch coverage: 6612, 6613, 6614, 6615, 6616, 6617, 6639, 6660, 6670, 6671, 6724, 6750, 6946.

## Behavior

Deleting or renaming a named profile is made safer against the open SQLite handles, raw probes, read pools and plugin managers that still point into its home, via the mechanisms below. This is a narrowing of races, not a proof that none remain: the first commit calls itself a partial repair, writers outside this process (including external plugin writers) are not shown to be covered, and cross-process drain is exercised by fixtures only.

- Bare/read-only `SessionDB` owners and shared registry handles are retired before removal; quarantine directory creation goes through the named-home tombstone fence.
- Resource leases are published *outside* the home before SQLite and raw probes open; failed closes and active readers are retained until drained; generation-bound plugin managers/hosts are retired and fenced; parked session stores are retried without restart.
- A short, separate custody lock keeps profile admission out of plugin-discovery locks (host callbacks resolving the manager during discovery could deadlock).
- Drained and abandoned leases are reclaimed (short-lived CLI exits before the daemon monitor's next tick; crash residue on later acquisition). The sweep only touches locked files and revalidates each opening inode after locking.
- Pooled-reader retirement is bound to the lease it opened under; a reader returned after a checkout miss is reclaimable instead of tripping a path ceiling with an idle reader available.
- Closed connection custody and shared probe custody are separated, so a closed transient owner no longer holds a lease while another owner pins shared header/pread probes.
- Late SQLite opens refuse to recreate a retired profile's parent directory (no ghost profile after rename/unserve).
- Holder-priority reads no longer take cache custody that could recreate a renamed profile.
- The state-reader admission denial reports which gate actually refused.

## How it works

`hermes_profile_resources.py` defines `ProfileResourceLease` (owner/reader accounting, `finish_if_drained`, `retry_closes`, `assert_reopen_allowed`). `hermes_state_registry.py` (`retire_resource_owner`), `hermes_state_dbfile.py` (probe closing), `hermes_state_readpool.py`, `hermes_cli/sqlite_safe_read.py` (`release_profile_pread_fds`) and `hermes_cli/plugins_profile_lifecycle.py` (`protect_profile_manager`, `assert_manager_live`, `retire_profile_manager`) cooperate; `hermes_cli/profiles_remove.py` and `gateway/run_profile_reconcile.py` drive removal and reconciliation; `kanban_annotation_fairness.py`/`kanban_annotation_disk.py` read priority from published stamps without loading custody.

## Code

- `hermes_profile_resources.py`, `hermes_state.py`, `hermes_state_dbfile.py`, `hermes_state_readpool.py`, `hermes_state_registry.py`
- `hermes_cli/sqlite_safe_read.py`, `sqlite_util.py`, `plugins.py`, `plugins_loader.py`, `plugin_host.py`, `plugins_profile_lifecycle.py`, `profiles_remove.py`
- `gateway/run_profile_reconcile.py`, `hermes_cli/kanban_annotation_disk.py`, `kanban_annotation_fairness.py`

## Tests

Read from source diffs; **not executed here**.

- `test_profile_delete_handles.py`, `test_profile_delete_ownership_gate.py`, `test_profile_delete_recreate_live.py`, `test_profile_lease_cleanup.py`, `test_profile_probe_drain_ack.py`, `test_profile_plugin_retirement.py`, `test_sqlite_util_canonical.py`, `test_reconcile_recovered_store.py`, `test_annotation_holder_fairness.py`; a 500-sidebar-read lease-census test and separate header-only/pread-only drain tests.
- Cross-process drain is covered by fixtures only; behavior with external plugin writers is called out as remaining work in the first commit and is **untested** here.

## Applying

Apply 6612-6617 first (partial repairs build on each other), then the others in ordinal order.

## Gotchas

- The first commit is explicitly partial; later commits close cross-process and plugin-manager gaps.
- Lease files are swept only while locked; inode revalidation after lock is essential.

## Why core, not a plugin?

State, profile removal and plugin-host lifecycle are core.
