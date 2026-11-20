# Kanban idle-CPU: accounting names and unchanged-board skips

Mirror entries 4979, 4980, 4981, 4982, 4990, 4995.

## Behavior

Structural optimizations and attribution fixes for background kanban/sync work. None claims a measured live CPU reduction.

- **4979** origin projection memoizes bounded exact-text binding JSON decode and digest, returning caller-owned copies; oversized/non-text values take the literal path; no projection, owner freshness, stream or deadline state is cached.
- **4980/4981** thread-target naming: list `args` combined with a `functools.partial` tuple raised TypeError and silently lost attribution. The inspection copy is normalized at the Thread boundary; only tuple/list args are inspected, so unknown iterables are not consumed.
- **4982** the links skip key ignores replica bookkeeping: it uses covered shadow activity revisions and main-file identity/stat instead of transport cursor progress. Missing coverage and legacy subscriptions keep literal reconciliation.
- **4990** `prepare_apply` on a containment-enabled replica no longer recreates eight triggers each page (which bumped `schema_version` by 16 and defeated the skip key): wanted trigger SQL is compared with stored definitions, only missing/different triggers are replaced under the writer transaction, and a settled replica skips the writer transaction and backfill, rechecking after taking the writer. Recovery of deleted marker observations is preserved.
- **4995** executor work-item completion (synchronous Future callbacks) is wrapped in an exclusive `executor_item/<function>` scope; bounded-sync calls are named; diagnostic failures never skip/retry work or replace exceptions; bounded target capacity raised to 256.

## How it works

Each change removes repeated work on an unchanged input: exact-text memo keys (4979), a skip key built from stable revisions instead of cursor progress (4982), a stored-vs-wanted trigger SQL comparison with a recheck under the writer (4990), and CPython work-item `run` wrapping in an exclusive scope for the legacy and task-triple layouts (4995). Accounting wrappers fail open: they never change execution or exceptions.

## Code

`hermes_cli/kanban_origin_ledger.py`, `kanban_origin_structure.py`, `kanban_sync_client.py`, `kanban_containment_guard.py`, `loop_accounting.py`, `agent/deadline.py`.

## Tests

Source tests (not executed here): `test_kanban_origin_structural_reuse`, `test_kanban_sync_links_accounting`, `test_kanban_sync_links_replica_revision`, `test_kanban_containment_paging`, `test_kanban_containment_schema_stability`, `test_loop_accounting_thread_targets`, `_completion`, `_residual`.

## Applying

Use the full segmented recipe in the core mods [README](README.md) ("Applying"), never single patches; several entries share files with earlier accounting work.

## Gotchas

- Live idle-CPU acceptance was explicitly not claimed; measure after deployment.
- Queue/semaphore costs stay outside the executor item scope.
- A read-only anti-join over task markers still scans; 4990 does not remove it.
