# Owning-chat stop-only control for ended shadow work

Patch coverage: 6208, 6209, 6210, 6211, 6212, 6242.

## Behavior

An owning interactive chat can snapshot an ended local-executing shadow-board card, then apply a digest-bound administrative stop. The card remains incomplete, nonexecuting and in triage with its dependencies unsatisfied. This is not completion, release, a renewed spend window or a canonical protection receipt.

The registered chat-only tool requires the live owner token bound by the actual interactive turn runner. Worker, delegated, cron, cancelled and copied-after-end contexts cannot mint that authority.

## How it works

The mutable token is shared by copied contexts and permanently invalidated on turn end/replacement/cancellation. Its lock precedes the board writer lock, so cancellation either prevents commit or waits for the in-flight commit. CAS application checks state and fixed escalation evidence, records one idempotent stop receipt and publishes through the ordinary outbox only under the advertised admin_stop_ingress capability.

SQL fences preserve stopped task identity, run evidence and dependency edges. Ordinary feed echoes that would rewrite frozen lifecycle are skipped while the feed cursor can advance; monotonic transport acknowledgement metadata remains allowed. Stop removes registered wake probes and stale worker-start tokens. A narrow repair clears only leftover start tokens on stopped rows without a pid, claim or current run.

## Code

Affected implementation and integration paths in the source series:

- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_spend_decision.py`
- `hermes_cli/kanban_stop_context.py`
- `hermes_cli/kanban_stop_ended.py`
- `hermes_cli/kanban_stop_guards.py`
- `hermes_cli/kanban_stop_sync.py`
- `hermes_cli/kanban_sync_round_send.py`
- `hermes_cli/observability/schemas/hermes.shared_metrics.v4.schema.json`
- `tools/kanban_stop_ended_tools.py`
- `tools/kanban_stop_registrations.py`
- `tools/kanban_tools.py`
- `toolsets.py`
- `tui_gateway/prompt_turn.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_kanban_stop_ended.py`
- `tests/hermes_cli/test_kanban_stop_sync.py`
- `tests/hermes_cli/test_kanban_source_receipt_sites.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

The initial kernel entry was incomplete; later entries add the turn binding, tool and sync hardening. Do not apply that first entry alone. LOCAL-SHADOW-ONLY and canonical_protected=false remain explicit; publication is capability-gated, not a deployment claim. Scope renewal does not cover this administrative stop.

## Why core, not a plugin?

Live turn authority, SQLite fencing, spend restrictions and sync application must be enforced together inside core, not as an optional tool hook.
