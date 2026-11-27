# Source-derived actionability and fixed waiting obligations

Patch coverage: 6177, 6190, 6196, 6216, 6217, 6230, 6232, 6247, 6283, 6314.

## Behavior

Actionability reports name the real release verb for eligible scope/spend stops instead of treating worker-written release prose as an unissuable permission gate. Safety, containment and receipt-only states do not acquire release authority.

Ordinary waiting obligations retain a fixed semantic source and deadline across parent status changes and edge edits within the same todo episode. Runnable ready/running executors do not need an administrative deadline merely because they are executing, while confirmed deadlines and unresolved holds remain visible.

## How it works

Recorder and reader share source selection; confirmed contracts compare the entire parent set rather than parent status snapshots. Link/unlink changes carry the earliest validated todo obligation, including overdue values, and unblocking into todo freezes an obligation. Episode exits prevent carrying a stale prior episode's deadline.

Private-history classification distinguishes resolved budget history from still-held scope or safety provenance. Scope renewal counts only with verified mandate-digest coverage; lane holds name lane admission. A missing live parent may borrow terminal lifecycle status from its replica, never replica stop/authority history. Admitted chat questions remain live wait producers, with block/human precedence preserved.

## Code

Affected implementation and integration paths in the source series:

- `agent/prompt_builder.py`
- `hermes_cli/kanban_actionability.py`
- `hermes_cli/kanban_actionability_cli.py`
- `hermes_cli/kanban_actionability_confirmed.py`
- `hermes_cli/kanban_actionability_creation.py`
- `hermes_cli/kanban_actionability_evidence.py`
- `hermes_cli/kanban_actionability_observation.py`
- `hermes_cli/kanban_actionability_private.py`
- `hermes_cli/kanban_actionability_selection.py`
- `hermes_cli/kanban_actionability_sources.py`
- `hermes_cli/kanban_wait_info.py`
- `tools/kanban_retain_tools.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_kanban_actionability_contract.py`
- `tests/hermes_cli/test_kanban_actionability_source_parity.py`
- `tests/hermes_cli/test_kanban_actionability_private.py`
- `tests/hermes_cli/test_kanban_actionability_todo_sources.py`
- `tests/hermes_cli/test_kanban_actionability_unlink_deadline.py`
- `tests/hermes_cli/test_kanban_actionability_shadow_parent.py`
- `tests/hermes_cli/test_kanban_wait_info.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

Diagnostics never release, renew, promote or supply execution authority. A recorded condition remains a note. Unknown evidence cannot become a pass, and a present live parent takes precedence over a terminal replica copy.

## Why core, not a plugin?

Creation-time obligation stamping, execution reducers and read-only census must use the same core semantic source and custody model.
