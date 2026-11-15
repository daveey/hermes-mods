# Creator-scoped cross-gateway scratch archive

Mirror batch 6; ordinals 4018, 4019, 4020.

## Behavior

A worker can queue archive of another card created by that exact task at the card’s home gateway, without acquiring general foreign-archive authority. Explicitly abandoned scratch triage cards of finished creators may be retired rather than dispatched again.

## How it works

The creator_archive action travels through the authenticated supervisor/operator courier. Replica preflight is advisory; the home verifies creator, claim and archive scope again inside the archive transaction. Request markers make replay idempotent. Triage eligibility requires local ownership, triage/scratch state, no human/claim/dependency exclusion, a finished creator (local or replica), and an affirmative exact archive command in the latest comment. Transactional rechecks prevent races and orphan cleanup comments.

## Code

Source paths touched by this feature group (including regression fixtures):

- `hermes_cli/kanban_creator_archive.py`
- `hermes_cli/kanban_operator_call.py`
- `hermes_cli/kanban_owner_archive_relay.py`
- `hermes_cli/kanban_triage_cleanup.py`
- `hermes_cli/kanban_triage_sweep.py`
- `tests/hermes_cli/test_kanban_creator_archive.py`
- `tests/hermes_cli/test_kanban_creator_archive_adversarial.py`
- `tests/hermes_cli/test_kanban_creator_archive_boundary.py`
- `tests/hermes_cli/test_kanban_creator_archive_predicates.py`

Mail entries in the ordered series:

- 4018: [`346c1a0d9d85b9e1c7473294310b277a556109a9`](patches/4018-fix-kanban-route-creator-scratch-cleanup-to-the-home-gateway-daveey.patch)
- 4019: [`b06992603056065fcd87f8fe55a654391f7a647c`](patches/4019-daveey-test-kanban-exercise-creator-cleanup-authorization-and-races.patch)
- 4020: [`65f4709ac57a319a3aa5fcf7d02bf7bbc1eeab9f`](patches/4020-daveey-test-kanban-isolate-creator-cleanup-predicate-boundaries.patch)

## Tests

- `tests/hermes_cli/test_kanban_creator_archive.py`: ready/triage/done roundtrips archive at home only, requests settle, wrong creators and forged origins reject, newly claimed targets stay running, and triage controls preserve excluded cards.
- `tests/hermes_cli/test_kanban_creator_archive_adversarial.py`: persisted state/events verify creator-removal/change races, in-transaction claim fences, request replay, missing/self/unscoped callers, replica-only parents, foreign targets and comment/human/parent races; failed preconditions leave no cleanup bookkeeping.
- `tests/hermes_cli/test_kanban_creator_archive_boundary.py`: tool-handler archive queues and completes the courier request; canonical caller/target tampering never archives either target; new dependents refuse at home. A changed target’s refusal cannot settle the original request, which stays accepted/pending.
- `tests/hermes_cli/test_kanban_creator_archive_predicates.py`: capability-owner scope is insufficient for exact creator authority, absent requesters never gain unscoped authority, and absent/non-triage targets are ineligible.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

archive_pending means queued, not archived. General foreign archive is still refused; negations, old comments and vague archive mentions do not grant cleanup authority.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.
