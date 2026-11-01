# One replica connection per sync round

Patches **1485–1486** (`a59e2ef56df`, `55bdf7fcd17`).

## Behavior

Both shadow and primary sync reuse the pulled replica during partition repairs instead of opening it a second time. Success, pull failure and repair failure close the connection. A retention-floor rebuild replaces the old connection and passes the replacement to repairs.

## How it works

`_pulled_replica` is a context manager around pull/resync and the pulled stamp. `sync_board` keeps its repair phase inside that context. `_pull_replica` remains a standalone wrapper. The repair-input revision cache and push semantics are unchanged.

## Code

`hermes_cli/kanban_sync_client.py`; local isolated fixtures in `tests/hermes_cli/test_kanban_sync_round_connections.py` avoid importing fixture definitions from another test module.

## Tests

`test_round_connection_replay` covers shadow and primary modes: ten unchanged rounds skip partition repair, ten changed rounds repair and publish the final title, and each normal round opens one replica. `test_round_closes_replica_on_success_and_failure` covers success, pull error and repair error. `test_resync_replacement_is_used_for_repairs_and_closed` checks replacement identity and closure. Executed results are recorded in [snapshot 1492](mirror-1492-verification.md).

## Applying

Use the complete series and bridge recipe in [README](README.md#applying), not these patches independently.

## Gotchas / plugin?

This changes core connection ownership, not a plugin or a persistent connection pool. No live performance or fleet rollout claim is made by the mirror verification.
