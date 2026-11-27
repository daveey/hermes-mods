# Read-first spend decisions and scope release fences

Patch coverage: 6174, 6286, 6315, 6322, 6323, 6324, 6340, 6353, 6354, 6376, 6377, 6378, 6379.

## Behavior

Spend-review grants remain subordinate to owner budget decisions and require evidence of a genuinely new run after the latest promotion. Manual promotion can decide a stranded ready/scheduled spend stop, but does not erase unrelated execution restrictions or parent checks.

Agent release and spend-budget operations require stateful read-first evidence. Guessing a comment id is insufficient: this session must previously have been shown the required evidence for the same verb. Restating or tightening a human limit preserves its human attribution; lifting it requires reading that decision.

## How it works

Decision events carry portable decision-comment references. Legacy recovery is narrowly producer-specific: reviewer immediate-predecessor comments and manual exact-content, unique budget notes use different contracts. Restored historical stops still require the actual local comment watermark, not a foreign integer. An unreadable human budget note makes the budget event itself a required read; event and comment namespaces do not collide, and a nearby note from another override cannot substitute.

Local claim deferrals identify spend/decision policy refusal before a Board lease. Reviewer qualification rejects post-stop overrides, zero-run retriage and same-second promotion/run ambiguity. Release receipts track shown sets per session, verb and local board origin. Stranded-park re-filing uses actual filing/rehome evidence. Courier create refusals distinguish definitive released/placeholder origins from named waits for sync settings, owner identity, creator sessions or foreign provenance.

## Code

Affected implementation and integration paths in the source series:

- `hermes_cli/kanban_create_authority.py`
- `hermes_cli/kanban_db_dispatch_stuck.py`
- `hermes_cli/kanban_db_promote.py`
- `hermes_cli/kanban_origin_owner.py`
- `hermes_cli/kanban_owner_chat.py`
- `hermes_cli/kanban_release.py`
- `hermes_cli/kanban_spend_budget.py`
- `hermes_cli/kanban_spend_budget_cli.py`
- `hermes_cli/kanban_spend_claim.py`
- `hermes_cli/kanban_spend_override_fence.py`
- `hermes_cli/kanban_spend_reviewer.py`
- `hermes_cli/kanban_stranded_park.py`
- `tools/kanban_tools.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_kanban_spend_override_fence.py`
- `tests/hermes_cli/test_kanban_spend_reviewer.py`
- `tests/hermes_cli/test_kanban_spend_claim_accounting.py`
- `tests/hermes_cli/test_kanban_restored_stop_budget.py`
- `tests/hermes_cli/test_kanban_release.py`
- `tests/hermes_cli/test_kanban_create_authority_wait.py`
- `tests/origin_transport/test_forwarding.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

At the end of this range, sync disabled/unreadable and missing creator sessions are changeable waits, not terminal no-write refusals; earlier intermediate behavior is superseded. Read-first evidence for release is not evidence for spend-budget. A reviewer grant is not clearance of safety, containment or dependency restrictions.

## Why core, not a plugin?

These checks must share transactional spend reducers, comment custody, release authority and courier result semantics; plugin-side advisory checks cannot enforce them.
