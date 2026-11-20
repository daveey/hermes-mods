# Kanban containment: policy, custody, transport and retention

Mirror snapshot 5011; ordinals 4902, 4903, 4904, 4905, 4906, 4907, 4908, 4909, 4910, 4911, 4912, 4913, 4914, 4915, 4916, 4917, 4918, 4919, 4920, 4921.

Entries:

- **4902**: feat(kanban): define strict containment policy wire contract
- **4903**: feat(kanban): add containment episode reducer and local storage fences
- **4904**: feat(kanban): integrate local containment transaction custody
- **4905**: fix(kanban): bind containment actor to coherent task lease
- **4906**: test(kanban): kill adjacent containment authority guard mutants
- **4907**: feat(kanban): stage canonical containment transport and replica markers
- **4908**: feat(kanban): integrate public containment and certified replica fences
- **4909**: fix(kanban): negotiate containment and expose terminal handoff
- **4910**: test(kanban): prove containment connection selection boundaries
- **4911**: fix(kanban): replay only unresolved containment requests
- **4912**: test(kanban): pin containment resume and replica guard boundaries
- **4913**: fix(metrics): include containment tools in shipped contract schema
- **4914**: fix(kanban): let released containment history retain, delete and export safely
- **4915**: test(kanban): freeze containment retention cutoff without wall sleep
- **4916**: fix(kanban): retire released sender custody during copy deletion
- **4917**: test(kanban): isolate held graph insertion fence
- **4918**: test(kanban): isolate containment scope and evidence predicates
- **4919**: test(kanban): prove projection and strict parser boundaries
- **4920**: test(kanban): isolate containment admission and dispatch fences
- **4921**: test(kanban): verify imported snapshots never inert source

## Behavior

- **Policy** (4902): strict wire contract, version integer 1, exact identity keys, same-board predicates, no silent unknown predicates, no self-resume. Legacy route boundaries stay as controls (review is dispatchable, completion is not containment).
- **Local episodes** (4903–4906): unreleased episode set with authenticated-release verification; stale predicate hashes and reused producer identities refused. Opt-in local fences are transaction- and task-scoped. Actor must be the current live lease. Generic lifecycle events do not release containment.
- **Canonical transport** (4907–4911): durable single-request delivery bound to Board event bytes and admitted claims/sessions. Server-owned markers hold task-before-history page gaps. Authenticated Board capability is required before canonical I/O; never fall back locally. Replay only unresolved requests. Successful own-run containment is terminal for worker tools and native MCP.
- **Metrics** (4913): v4 shared-metrics enum includes the two public containment tool names.
- **Retention and delete** (4914–4917): released history may GC, delete and export; active containment cannot. Worker-copy eviction needs verified history-delete custody; deletion retires only matching observed stop requests. Held graph inserts need a replica permit.
- **Predicate tests** (4918–4921): isolated scope, evidence, parser, admission, dispatch, export/import fences. Imported snapshots never inert the source board.

## How it works

- New modules under `hermes_cli/kanban_containment_*.py`, CLI/tool adapters, DB triggers, replica projection, `agent/kanban_stop.py`, `tools/kanban_containment_tools.py`.
- Website user-guide `kanban-containment.md`.

## Code

Shipped non-test paths (union over these entries):

- `agent/kanban_stop.py`
- `agent/transports/hermes_tools_mcp_server.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_containment_actor.py`
- `hermes_cli/kanban_containment_canonical.py`
- `hermes_cli/kanban_containment_cli.py`
- `hermes_cli/kanban_containment_connections.py`
- `hermes_cli/kanban_containment_guard.py`
- `hermes_cli/kanban_containment_lifecycle.py`
- `hermes_cli/kanban_containment_outbox.py`
- `hermes_cli/kanban_containment_policy.py`
- `hermes_cli/kanban_containment_remote.py`
- `hermes_cli/kanban_containment_replica.py`
- `hermes_cli/kanban_containment_requests.py`
- `hermes_cli/kanban_containment_service.py`
- `hermes_cli/kanban_containment_state.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_dispatch_queries.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_decompose.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_resume_exec.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_hub_routes.py`
- `hermes_cli/kanban_task_fields.py`
- `hermes_cli/kanban_transfer.py`
- `hermes_cli/kanban_triage_sweep.py`
- `hermes_cli/kanban_worker_node.py`
- `hermes_cli/observability/schemas/hermes.shared_metrics.v4.schema.json`
- `tools/kanban_containment_tools.py`
- `tools/kanban_tools.py`
- `toolsets.py`
- `website/docs/user-guide/features/kanban-containment.md`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/agent/test_kanban_containment_stop.py`
- `tests/hermes_cli/test_kanban_cli_exit_status.py`
- `tests/hermes_cli/test_kanban_containment_actor.py`
- `tests/hermes_cli/test_kanban_containment_adapter_predicates.py`
- `tests/hermes_cli/test_kanban_containment_admission_predicates.py`
- `tests/hermes_cli/test_kanban_containment_canonical.py`
- `tests/hermes_cli/test_kanban_containment_capability.py`
- `tests/hermes_cli/test_kanban_containment_claim_predicates.py`
- `tests/hermes_cli/test_kanban_containment_connections.py`
- `tests/hermes_cli/test_kanban_containment_delete_paths.py`
- `tests/hermes_cli/test_kanban_containment_envelope.py`
- `tests/hermes_cli/test_kanban_containment_feed_predicates.py`
- `tests/hermes_cli/test_kanban_containment_guard_predicates.py`
- `tests/hermes_cli/test_kanban_containment_history_delete.py`
- `tests/hermes_cli/test_kanban_containment_legacy_paths.py`
- `tests/hermes_cli/test_kanban_containment_lifecycle.py`
- `tests/hermes_cli/test_kanban_containment_lifecycle_predicates.py`
- `tests/hermes_cli/test_kanban_containment_maintenance.py`
- `tests/hermes_cli/test_kanban_containment_paging.py`
- `tests/hermes_cli/test_kanban_containment_parser_predicates.py`
- `tests/hermes_cli/test_kanban_containment_policy.py`
- `tests/hermes_cli/test_kanban_containment_remote.py`
- `tests/hermes_cli/test_kanban_containment_replica.py`
- `tests/hermes_cli/test_kanban_containment_replica_permits.py`
- `tests/hermes_cli/test_kanban_containment_request_retirement.py`
- `tests/hermes_cli/test_kanban_containment_retention.py`
- `tests/hermes_cli/test_kanban_containment_scope.py`
- `tests/hermes_cli/test_kanban_containment_service.py`
- `tests/hermes_cli/test_kanban_containment_sql_adjacent.py`
- `tests/hermes_cli/test_kanban_containment_state.py`
- `tests/hermes_cli/test_kanban_containment_transport_predicates.py`
- `tests/hermes_cli/test_kanban_source_receipt_sites.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- Capability absence never falls back to local containment.
- Export/import must not scrub the source connection.
- Held work is excluded from ordinary triage, decompose and sweep.
- Schema drift of builtin tool names must update the v4 metrics enum in the same change.

## Plugin?

None.
