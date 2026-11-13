# Kanban worker: fail-closed resolver exit and owner capsule preadmission proof

Patches: **3321, 3327**.

## Behavior
- A quota-recovery DNS resolver child that exits before the parent flushes now fails closed ("DNS resolution failed") instead of raising `BrokenPipeError`.
- An owner's pre-session `capsule_observation_missing` refusal is booked with a preadmission proof and requeued, not counted as a crash. The worker client exits owner-unavailable (69) when the owner keeps refusing; an admitted capsule refusal gets no such proof.

## How it works
- 3321 ignores EPIPE on the request write and lets the child's exit status decide.
- 3327 extends `kanban_host_refusal` and the worker client to ask again before admission and book `preadmission_proof` with kind `owner_capsule_unavailable`.

## Code
- `hermes_cli/kanban_host_refusal.py`
- `hermes_cli/kanban_quota_recovery.py`
- `hermes_cli/kanban_worker_client.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_quota_dns_deadline.py`: `test_resolver_failure_is_closed_and_reaped`.
- `tests/hermes_cli/test_kanban_host_refusal.py`: `test_preadmission_capsule_refusal_books_its_proof_and_requeues`, `test_an_admitted_capsule_refusal_gets_no_preadmission_proof`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
