# Managed worker run scope and aggregate memory bound

Patch 1372; source `cb4ac494b00a2dbcdfe0c7cf508e908db88d0663`.

## Behavior

Managed Linux workers join the verified per-run cgroup before bootstrap. A claimed scope that cannot be joined fails closed. All systemd run scopes share an aggregate memory slice.

## How it works

Resolve the running claim and submitter PID, waiting boundedly for PID publication; migrate the unreaped child via cgroup.procs and confirm membership. Failed migration kills and reaps it. systemd-run receives hermes-kanban.slice, with MemoryHigh=70% and MemoryMax=80%.

## Code

gateway/session_kanban_scope.py; gateway/session_managed_worker.py; hermes_cli/kanban_aggregate_scope.py; hermes_cli/kanban_db_dispatch.py

## Tests

Source regression inventory: tests/gateway/test_managed_kanban_scope_placement.py; tests/hermes_cli/test_kanban_aggregate_scope.py; tests/gateway/fixtures/kanban_owner_probe.py; restart-handoff and worker-scope-failure fixture adaptations.

These runtime suites were not rerun in this mirror pass. Executed mirror inventory/replay/tree checks are recorded in [mirror-1375-verification.md](mirror-1375-verification.md); they do not certify runtime behavior.

## Applying

Apply the complete snapshot using the [README replay recipe](README.md#applying), not this patch independently.

## Gotchas

No claimed systemd scope means unchanged spawning. The gateway itself is not moved into the aggregate pool. This mirror does not certify live host memory settings.

## Plugin?

This patch mirrors the source implementation; no separate public mod installation is required.

## Ended-run evidence survives copy eviction (1522)

### Behavior

An ended foreign-copy run can still have its leftover systemd scope stopped after the grace period,
even after the worker evicts the copied card and its run rows. An absent run with no retained evidence
is never treated as ended. Live/open rows, live retained worker PIDs and the sweeper's own scope keep
their existing protections.

### How it works and Code

`hermes_cli/kanban_run_tombstones.py` retains `(task_id, local run_id, ended_at, worker_pid)` in
`kanban_ended_run_tombstones`, inside `kanban_worker_node.evict`'s existing write transaction before
deleting run rows. The local-only table has no task foreign key or sync capture. `ended_run` prefers
a live row (including an open run) and only then consults tombstones; reads of older databases do
not create a table. `kanban_worker_scope.stop_ended_run_scopes` uses this lookup without changing
its grace, PID or scope-membership gates.

### Tests

Source `tests/hermes_cli/test_kanban_evicted_run_scope.py` exercises eviction followed by reaping
after grace, including reopen/persistence, and protects an open home run with a live lease.
`tests/hermes_cli/test_kanban_run_tombstones.py` covers missing/legacy/open rows and transaction
rollback of retention with eviction. Not run in this documentation pass; no live systemd scope
teardown is claimed.

### Applying and Gotchas

Apply 1522 through the complete [README replay recipe](README.md#applying). Scope names contain
machine-local SQLite run IDs, not Board run UIDs: never substitute a replica's numeric run id.
This retains evidence, not a new authority to stop unknown processes.
