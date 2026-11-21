# Fairness capture ownership, rollback and portable budgets

Mirror entries: 5040, 5041, 5042, 5043, 5044, 5045, 5046.

## Behavior

- Nested serving scopes and the gateway dispatcher close only capture epochs they created, not borrowed epochs.
- Dispatcher cancellation waits for background startup/close to finish before releasing the singleton lease.
- Refused arming restores the previous plan, or removes a new plan; a committed journal keeps its plan even if a later status read or drain fails.
- DB-only capture supports a Windows-style budget locking path. Runtime capture still requires Unix IO and advisory-lock primitives; disabled capture stays inert.

## How it works

`boot_owned()` uses a context-local ownership mode without changing the public `boot()` signature. The dispatcher shields process-service thread work and observes its late result even after cancellation, then closes owned capture state in a lease-release `finally`.

Runtime startup checks `O_NONBLOCK`, `O_NOFOLLOW`, `O_DIRECTORY`, and the required `fcntl` attributes before capture IO. Arming publishes through the budget first, then compares SQLite's transactional schema cookie on error: only positively established rollback restores the old plan. Interrupted open transactions are rolled back before that comparison.

Budget writes use `flock` where available. The Windows branch seeks to byte zero for both lock and unlock, uses `msvcrt.LK_NBLCK`, and retries only `EACCES` with a 0.1-second sleep. Other errors propagate. File fsync and atomic replacement remain; directory fsync is skipped when `O_DIRECTORY` is unavailable. Test parameter IDs are bounded semantic names rather than oversized payload contents.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `gateway/kanban_watchers.py`
- `hermes_cli/kanban_fairness_activation.py`
- `scripts/kanban_fairness.py`
- `hermes_cli/kanban_fairness_budget.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

The ownership tests cover nested serving, actual gateway slash-dispatch composition, and new-epoch closure. Dispatcher tests cover cancellation during boot and close, raising boot/close, and lease retention until close finishes. Arm tests inject pre- and post-commit errors, including `KeyboardInterrupt` and same-ID replacement, and exercise subsequent status/re-arm recovery. Review-clause tests cover reader/producer custody, armed export refusal and budget mismatch.

Portable-budget tests exercise DB CLI arm/status/drain/seal/clear with simulated Windows primitives, byte-zero locking, prolonged contention during write/restore/arm rollback, and unsupported-volume errors without retry. This is simulated platform coverage, not native Windows execution. Live runtime activation is untested by this mirror task.

Source test/coverage paths:

- `tests/gateway/test_kanban_dispatcher_capture_cancel.py`
- `tests/hermes_cli/test_kanban_fairness_activation_ownership.py`
- `tests/hermes_cli/test_kanban_fairness_activation_platform.py`
- `tests/hermes_cli/test_kanban_fairness_arm_rollback.py`
- `tests/hermes_cli/test_kanban_fairness_review_clauses.py`
- `tests/hermes_cli/test_kanban_fairness_runtime.py`
- `tests/hermes_cli/test_kanban_fairness_budget_portable.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

An arming error does not imply rollback: inspect the journal and retained plan before retrying. Windows DB portability does not make runtime capture portable. Advisory locks cannot constrain unrelated writers that do not follow the protocol. Capture evidence is not proof of complete scheduler coverage.

## Why core instead of a plugin

Capture epochs, dispatcher leases, SQLite journal admission and atomic budget IO are owned by core. A plugin cannot safely impose their shared cancellation and transaction boundaries.
