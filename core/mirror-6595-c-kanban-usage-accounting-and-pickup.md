# Usage-accounting gate and pool pickup budgets

**Scope:** Unsettled-run accounting gate, observe-by-default mode, pickup slots for debt cards.

**Source ordinals:** 6484, 6540, 6541, 6542, 6556

## Behavior

- In explicit enforce mode, admission waits for consistent final usage and canonical closure of pending admitted/bound turns and ambiguous released runs, subject to an optional enforce_after cutoff; receipts reduce as run-scoped lower bounds. Observe mode retains debt without refusing admission.
- The gate observes by default rather than refusing: successful non-continuable exit-0 results with all four valid token counters can publish a final, while interruption/continuation paths need not do so. A separately validated bound-worker no-turn proof can settle construction failure without inventing usage; legacy receipts retain their compatibility rules. Invalid mode values and ordinary config-read failures select enforcement.
- In enforce mode, pool pickup copies usage-debt cards outside the main budget using separate free slots so they neither consume clean-card budget nor the guard-held copy slots; observe mode does not classify raw pending debt as a pickup hold.

## How it works

- `kanban_usage_accounting.py`, `kanban_spend_claim.py`, `kanban_db_dispatch_stuck.py`, `kanban_worker_node.py`, `gateway/session_kanban.py`.

## Code

Files touched by the series (paths relative to the source tree):

- `gateway/session_kanban.py`
- `hermes_cli/kanban_db_dispatch_stuck.py`
- `hermes_cli/kanban_spend_budget.py`
- `hermes_cli/kanban_spend_claim.py`
- `hermes_cli/kanban_usage_accounting.py`
- `hermes_cli/kanban_worker_node.py`

## Tests

Source test files changed or added by the series:

- `tests/agent/test_kanban_darkwake_recovery.py`
- `tests/conftest.py`
- `tests/gateway/test_session_kanban_restart_resume.py`
- `tests/gateway/test_session_kanban_restart_resume_unused_grant.py`
- `tests/hermes_cli/test_kanban_decided_triage.py`
- `tests/hermes_cli/test_kanban_pickup_usage_pending.py`
- `tests/hermes_cli/test_kanban_spend_budget.py`
- `tests/hermes_cli/test_kanban_usage_accounting.py`
- `tests/hermes_cli/test_kanban_usage_accounting_mode.py`
- `tests/hermes_cli/test_kanban_usage_accounting_sync.py`
- `tests/hermes_cli/test_kanban_worker_start_failure_cause.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6484-6556, 5 commits: 6484, 6540, 6541, 6542, 6556) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Unknown accounting still holds retries in refusing mode.
- Usage-debt copies are re-copied each tick under their own free-slot cap, separate from the guard-hold slots, so they cannot starve a lower-ranked guard-held copy.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Admission gating is part of core claims.
