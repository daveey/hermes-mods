# Advisory integrity inventory stops at its deadline

Patches: 5950, 5951, 5952.

## Behavior

- The advisory (non-strict) module integrity inventory used when a capsule identifies itself is bounded by a monotonic deadline, covering both the audit-lock acquisition and the work between modules.
- Expiry fails closed: the advisory answer is withheld rather than reported as positive. Strict, actual admission stays unbounded and is not given a deadline.
- Follow-up tests cover lock refusal, plugin-budget expiry and the cancellation boundary guards.

## How it works

The inventory loop in the attestation module checks the advisory deadline before each module inspection and before taking the audit lock, and stops walking when it is exceeded. The capsule contract passes the advisory budget through; the strict path calls the same inventory without a deadline.

## Code

Paths touched by these entries (non-test):

- `gateway/capsule_contract.py`
- `hermes_cli/code_attestation.py`

## Tests

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/gateway/test_capsule_contract_availability.py`
- `tests/gateway/test_capsule_identify_budget.py`
- `tests/gateway/test_capsule_integrity_gate.py`
- `tests/hermes_cli/test_attestation_inventory_budget.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Single filesystem operations and plugin verification are not interruptible; the deadline is checked between them, so a single slow call can still overrun it.
- A deadline-expired advisory result is an absence of evidence, never a positive claim.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes the gateway capsule contract and the code-attestation inventory inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
