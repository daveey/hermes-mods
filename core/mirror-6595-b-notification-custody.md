# Durable notice fanout and opt-in native scheduling

Patch coverage: 6218, 6219, 6227, 6252, 6253, 6254, 6255, 6256, 6257, 6258, 6259, 6260, 6279, 6290, 6291, 6357, 6358.

## Behavior

Notice fanout skips a chat as foreign only after complete profile discovery and readable stores prove local absence. Unknown hosting retains the durable cursor for retry. Automatic synthesized turns can defer behind a verified local gateway owner without taking custody; typed sends keep their ordinary refusal.

The native notification scheduling machinery is dormant by default at the end of this range. Explicit opt-in adds wake hints and one shared census clock while the existing serialized executors retain payload, claim and acknowledgement custody.

## How it works

Profile enumeration separates complete absence from permission/store failures. Held-parent episodes publish one advisory per episode and audience; subscription disappearance does not rearm it. Advisory events are excluded from release/park clocks, and late advisory folds preserve queued completion/review handoff bodies.

The native owner watches source rosters, tracks readiness and retry floors, and can transition live through display.gateway_notification_f with HERMES_FACTS_GATEWAY as an explicit emergency/consumer gate. Both default off. Unknown policy/watch failures revoke native readiness and recover on a shared retry; unsupported watches rely on census. Role transfer arms successors without duplicating executors and rollback preserves earlier obligations.

Notification revision probes acquire the session writer lock nonblocking before reading SQLite in_transaction; busy writers cannot certify committed absence. This prevents connection-mutex/GIL inversion during Python callbacks.

## Code

Affected implementation and integration paths in the source series:

- `gateway/kanban_notification_schedule.py`
- `gateway/kanban_remote_owner.py`
- `gateway/kanban_wake_fold.py`
- `gateway/kanban_watchers.py`
- `gateway/kanban_watchers_notifier.py`
- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban_awaiting.py`
- `hermes_cli/kanban_continuation.py`
- `hermes_cli/kanban_db_dispatch_guard_notice.py`
- `hermes_cli/kanban_decision_maintenance.py`
- `hermes_cli/kanban_parent_held.py`
- `hermes_cli/kanban_release.py`
- `hermes_cli/kanban_triage_sweep.py`
- `tui_gateway/gateway_background_holder.py`
- `tui_gateway/kanban_chat_hosting.py`
- `tui_gateway/kanban_unloaded.py`
- `tui_gateway/kanban_unloaded_gates.py`
- `tui_gateway/kanban_unloaded_schedule.py`
- `tui_gateway/notice_fanout.py`
- `tui_gateway/notification_gateway_policy.py`
- `tui_gateway/notification_gateway_sources.py`
- `tui_gateway/notification_idle.py`
- `tui_gateway/notification_loaded_f.py`
- `tui_gateway/prompt_turn.py`
- `tui_gateway/session_notifications.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/tui_gateway/test_notice_fanout_durability.py`
- `tests/tui_gateway/test_automatic_ledger_defer.py`
- `tests/gateway/test_kanban_wake_fold_advisory_handoffs.py`
- `tests/tui_gateway/test_gateway_dormant_defaults.py`
- `tests/tui_gateway/test_gateway_live_rollback.py`
- `tests/tui_gateway/test_gateway_policy_backend_recovery.py`
- `tests/hermes_cli/test_kanban_parent_held_residual.py`
- `tests/tui_gateway/test_notify_state_revision_mutex.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

Native wake hints are not payload freshness proof or a new delivery cursor. Do not claim default activation: earlier opt-in/default-on intermediate code is superseded by dormant defaults. A foreign or unproven writer does not qualify for automatic deferral. Presentation-only advisory messages must not postpone real escalation.

## Why core, not a plugin?

Durable notification cursors, turn leases, source lifecycle and SQLite writer locking live inside gateway core and must retain a single executor.
