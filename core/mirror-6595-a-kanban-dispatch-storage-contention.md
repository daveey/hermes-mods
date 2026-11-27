# Kanban dispatcher: bounded storage waits and typed verdicts

Patches: 6006, 6007, 6026, 6027, 6028, 6029.

## Behavior

- Spend-route repair checks whether it is needed before entering the global writer and rechecks inside; unchanged routes cause no write.
- Dispatcher preclaim transaction-boundary acquisition waits share a connection-scoped one-second allowance, charged by monotonic elapsed acquisition time rather than a whole-tick deadline. RPC, CPU and transaction-body time do not consume that allowance; contention returns a typed storage_write_contended result logged outside the database lock.
- The cancellation scope ends before pickup/capacity/claim work, so an in-flight spawn is never replaced by an empty result when a writer contends during pid publication.
- Admission-screen deferrals and unclassified claim refusals are recorded through the bounded skip recorder; locked dry-runs expose timestamped historical last-pass evidence or a typed unavailable diagnostic. A claim-time capsule refusal is not also reported as a Board deferral.

## How it works

kanban_dispatch_storage, kanban_dispatch_verdict, kanban_dispatch_skip and kanban_db_dispatch implement the budget and recording.

## Code

Paths touched by these entries (non-test):

- `gateway/kanban_watchers_dispatcher.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_stuck.py`
- `hermes_cli/kanban_dispatch_owner.py`
- `hermes_cli/kanban_dispatch_skip.py`
- `hermes_cli/kanban_dispatch_storage.py`
- `hermes_cli/kanban_dispatch_verdict.py`
- `hermes_cli/kanban_ops.py`
- `hermes_cli/kanban_spend_decision.py`

## Tests

Regression test-path inventory from these commits; path presence is not behavior proof. Test bodies were not audited and **tests were NOT executed**:

- `tests/gateway/fixtures/kanban_recovery_probe.py`
- `tests/hermes_cli/test_kanban_capsule_claim_race.py`
- `tests/hermes_cli/test_kanban_dispatch_postclaim_custody.py`
- `tests/hermes_cli/test_kanban_dispatch_verdict.py`
- `tests/hermes_cli/test_kanban_dispatch_write_contention.py`
- `tests/hermes_cli/test_kanban_spend_route_repair.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Holding the tick lock is not asserted to mean progress.
- Typed claim refusal is now expected by the recovery probe fixture.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes the dispatcher and its storage layer inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
