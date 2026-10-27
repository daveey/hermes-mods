# Authenticated dependency-review repair continuation

## Behavior

Patches **0378–0379** let an owning worker resume once after independent review requests changes to its existing PR. A dependency park must capture the authenticated owner run, parent IDs and PR URLs. Completed, actually claimed parent runs must supply structured `changes_required`, PR and full reviewed-SHA evidence after the park. This is permission to repair, not merge approval or current-head CI evidence.

## How it works

`checkpoint` captures scope inside the block transaction. `resumes_review` validates the latest run, unchanged dependencies, completed review provenance and the guarded PR set. The next claim consumes the permission. Legacy unauthenticated parks, comment-only evidence and changed PR/parent scope fail closed. Patch 0379 moves transactional claim release into `apply_block_update` without changing this contract.

## Code

- `hermes_cli/kanban_dependency_review.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_dispatch_pr_state.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Executed on the fork revision containing both commits and archive fix `f63cdf362c`, using an existing managed test environment and isolated HERMES_HOME:

`python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_kanban_dependency_review.py tests/hermes_cli/test_kanban_dependency_review_evidence.py`

Result: **13 passed**. These are database/dispatch regression tests; no live dispatcher continuation or Desktop UI was exercised by this mirror verification.

## Applying

Apply 0378 then 0379 after the preceding fork series. They mirror commits `cd68a2ce18` and `cb8fd02e9f`. Activate through the normal safe core update, not by changing an active checkout.

## Gotchas

A repeated PR comment is not new permission. An additional claim consumes the park checkpoint even if the worker later crashes. Do not fabricate checkpoint rows or use scheduling as review authorization.

## Why core rather than plugin?

The ownership check and claim release share the database transaction; a plugin cannot safely recreate this transition afterward.
