# Continuation exhaustion and automatic stops belong to agent recovery

Patches: **0269 0301 0307 0308 0334 0365**.

## Behavior

A current-run continuation-budget refusal followed by an exclusively budget-management ask routes to agent triage instead of presenting a human unblock request. It does not mark work complete. Real approval, credential, capability and product decisions remain human-visible, even when their explanation also mentions exhaustion.

Automatic give-up, respawn-guard holds, repeated agent-only blocks and impossible dependency-loop stops use agent recovery rather than inventing a human question. Budget recovery still works after that routing.

## How it works

`kanban_continuation_recovery` requires both the current run's `continuation_exhausted` event and a narrowly full-matched reason. Unknown or mixed prose is not swallowed. Run ownership/CAS prevents an old worker's refusal from affecting a new run. Block routing preserves genuine human asks and separates purely automatic recovery.

| Patch | Role |
|---|---|
| 0269 | Current-run exhaustion classification and agent-triage routing. |
| 0301 | Preserve human blocks and repair callers after helper movement, including dispatch-quota handling. |
| 0307 | Make the exhaustion test's clock-wait control explicitly a pure clock wait; test-only. |
| 0308 | Preserve approval requests embedded in exhaustion context. |
| 0334 | Route automatic stop/give-up/guard/dependency-loop paths to agent triage. |
| 0365 | Preserve budget recovery after agent triage; align lifecycle, scope-failure and park regressions. |

## Code

`hermes_cli/kanban_continuation.py`, `kanban_continuation_recovery.py`, `kanban_block_routing.py`, `kanban_db.py`, `kanban_db_dispatch.py` and `kanban_db_dispatch_quota.py`.

## Tests

Source tests, **not executed in this documentation pass**:

- `tests/hermes_cli/test_kanban_continuation_exhaustion.py`: budget-only asks; real human questions and clock waits; budget words without refusal; stale workers/new runs; approval context.
- `tests/hermes_cli/test_kanban_agent_stop_routing.py`: give-up and guard holds; agent-versus-approval repeated blocks; impossible dependency loops; budget recovery follow-up.
- `tests/hermes_cli/test_kanban_assignment_routing.py`: human-block preservation after helper moves.
- `tests/hermes_cli/test_kanban_block_classify.py`, `test_kanban_core_functionality.py`, `test_kanban_db.py`, `test_kanban_quota_guard.py`: automatic-stop integration.
- `tests/hermes_cli/test_kanban_park_invariants.py`, `test_kanban_review_lifecycle_complete.py`, `test_kanban_worker_exit_trailer.py`, `test_kanban_worker_scope_failure.py`, `test_kanban_worktree_create_validation.py`: 0365 lifecycle expectations.

Use `bash scripts/run_tests.sh <files>` in the applied isolated checkout. Executed full-series and selected test results are recorded in [mirror verification](mirror-377-verification.md); no live dispatcher exercise or UI verification is claimed here.

## Applying

Apply in ordered-series context, including the existing continuation and block-routing changes. This is separate from [PR park handoff](kanban-pr-park-handoff.md), which releases duplicate-PR holds, and [assignment recovery](kanban-assignment-capabilities.md), which repairs worker policy mismatches.

## Gotchas

Do not implement this as a keyword search for “budget” or a blanket conversion of `needs_input`. A historical refusal is not current-run authority. Agent triage is unfinished work and does not imply the underlying task has been resumed or delivered.

## Plugin relationship

Core transaction and dispatch routing own these transitions. A plugin can display the resulting state but cannot safely replace run-scoped classification with a notification filter.
