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
