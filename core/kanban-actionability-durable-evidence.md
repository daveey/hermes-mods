# Actionable escalation and durable owner evidence

## Behavior

- **0393:** Human escalation requires actionable evidence, not merely a blocked status. Human requests, counts, migration and recovery retire or preserve evidence through the integrated routing lifecycle. CLI, tool and HTTP request handling are covered by the entry tests below; live Desktop presentation is untested here.
- **0397:** Owner liveness can come from a fresh matching readable scope heartbeat, avoiding per-task timestamp churn. Legacy per-task timestamps remain supported.
- **0398–0399:** Legacy aliases reconcile only through exact owner-attested evidence and a distinct wire action. Reconciliation does not release unresolved obligations. Older adapters must not accidentally accept the new operation.
- **0400:** A failed nonblocking ledger read retains the prior sync timestamp, avoiding repeated replica hashing under writer contention.
- **0394–0396:** Test-only followups stabilize timestamp/reaping behavior and update the projects fixture to the atomic snapshot API.

## How it works / Code

0393 integrates source merge `2b6644387f`, including overlapping branch histories and their merge resolutions. See [provenance and verification](mirror-400-verification.md); do not independently replay its branch commits.

Actionability is implemented in `hermes_cli/kanban_human_request.py`,
`kanban_human_request_migration.py`, the DB/routing/activity helpers,
`tools/kanban_tools.py`, `tools/kanban_tools_schemas.py` and
`plugins/kanban/dashboard/plugin_api*.py`.
Durable evidence lives in `hermes_cli/kanban_origin_ledger.py`,
`kanban_origin_legacy.py`, `kanban_origin_adapter.py` and `kanban_session_activity.py`.

## Tests

The applied-series run documented in [mirror-400-verification.md](mirror-400-verification.md)
passed 290 tests across 15 files. Human-request entries exercise CLI/tool/HTTP handling;
migration HTTP auth and request lifecycle cover authorization and retirement. Actionability
counts, graph requests, session activity and triage cover projection. Origin legacy and adapter
tests cover exact attestation and old-wire rejection; scope heartbeat and projection-contention
tests cover freshness and contention. Auto-continue, projects RPC and LSP tests cover 0394–0396.
No terminal/execute_code end-to-end acceptance or live legacy mutation was performed.

## Applying

Apply the full numbered series from the documented merge base, not these patches in isolation.
The 400-patch candidate preserves all old patch numbers. Source 0400 remains unpublished/pending
independent review at export time; this documentation does not authorize publication or deployment.

## Gotchas / plugin?

Scope-heartbeat producers require a compatible hub client (source specifies >= 1.4.3);
coordinate producer rollout before deployment. Unknown/unreadable evidence is not completion.
The source's Kanban dashboard plugin changes are part of the core patch, not a separate mods
plugin installation. Existing public scrubs and historical mirror adaptations remain intact.
