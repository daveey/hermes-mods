# Advisory maintenance, helper edges and human containment closure

Patch coverage: 6226, 6299, 6300, 6337, 6355, 6356.

## Behavior

Repeated scope renewals without entering done/review can file one advisory remediation card for the current sequence, without releasing or gating the source. Explicitly declared helper cards cannot become prerequisites of the source they assist.

A human operator can archive a contained card with a reason through the scoped containment closure path. Dependents remain unsatisfied; when present they are listed and require explicit --yes acknowledgement.

## How it works

Renewal alarms recheck the sequence under the writer transaction, reset on real milestones and use an idempotency identity. The kanban-helper-for declaration is machine-readable and refusal-only: edge creation checks both public/internal paths and does not remove existing dependencies.

DispatchWriteContended propagates through hold notices, suspension ledger, parent-held and renewal sweeps as pass-wide cancellation, rather than repeating writes against a busy store. Human closure validates caller/reason, rejects agent use and safety-restricted/live-authority states, and requires the synced Board capability. Contained-wait diagnostics say stalled only when the authority itself or every unmet prerequisite is certainly stuck; cycles, unknown parents and depth limits do not prove stalling.

## Code

Affected implementation and integration paths in the source series:

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_cli_archive.py`
- `hermes_cli/kanban_contained_wait.py`
- `hermes_cli/kanban_containment_human.py`
- `hermes_cli/kanban_containment_terminal.py`
- `hermes_cli/kanban_containment_terminal_remote.py`
- `hermes_cli/kanban_db_archive.py`
- `hermes_cli/kanban_decision_maintenance.py`
- `hermes_cli/kanban_dependency_validation.py`
- `hermes_cli/kanban_helper_parent.py`
- `hermes_cli/kanban_hold_notices.py`
- `hermes_cli/kanban_parent_held.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_remote_stop.py`
- `hermes_cli/kanban_scope_renewal_alarm.py`
- `hermes_cli/kanban_suspend_ledger.py`
- `hermes_cli/kanban_wait_info.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_kanban_scope_renewal_alarm.py`
- `tests/hermes_cli/test_kanban_helper_parent.py`
- `tests/hermes_cli/test_kanban_maintenance_contention.py`
- `tests/hermes_cli/test_kanban_decision_contention.py`
- `tests/hermes_cli/test_kanban_containment_human_archive.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

An advisory alarm is not a mandate or budget grant. Declaring an existing helper does not waive its existing edges. Human containment archive is not successful completion; its dependents can remain waiting indefinitely. Plain archive and --reason have different authority requirements.

## Why core, not a plugin?

These rules alter dependency admission, dispatcher cancellation and containment terminal authority, which optional plugin hooks cannot safely replace.
