# Reuse escalation successors by premise episode

Patch coverage: **5852–5854**.

## Behavior

Repeated escalation for the same target and premise reuses its latest open successor, including legacy owners without an idempotency key. Completed or archived owners remain terminal; a later episode creates a distinct owner while retaining each reason in the ledger.

## How it works

Selection joins source escalation events to their recorded owner task, scoped to the target and awaiting premise. The latest episode is selected using the portable origin ordering helper over event timestamp/uid/id instead of local insertion id. Only when no ledger owner is found does lookup use the legacy key. New successors receive a deterministic key chained after the prior owner, fencing retries. Existing caller transactions keep owner creation, links and audit reasons atomic.

## Code

`hermes_cli/kanban_awaiting.py`: escalation owner lookup and successor idempotency. Regression data lives in `tests/hermes_cli/fixtures/repark-nine-records.json`; fixture narratives are not operational instructions.

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live deployment or performance result is claimed.

`tests/hermes_cli/test_kanban_escalation_successor.py` covers open-owner reuse, keyless adoption without title matching, distinct target/premise identities, terminal episode boundaries, crash rollback, concurrent retry reason retention, nine-record open-versus-completed replay, and schedule tool/database entry points. Patch 5853 adds delayed older deliveries with/without uid. Patch 5854 deliberately removes the successor key in that test so fallback cannot hide a broken portable-clock selection.

## Applying

Apply the covered patches in the full ordered core series using the [core applying recipe](README.md#applying), including its replay bridges and required repair overlay. These entries depend on earlier core changes; they are not advertised as an independent cherry-pick set. Reimplementation should preserve the invariants above and run the named source suites in a disposable source checkout.

## Gotchas

Do not infer historical savings from a counterfactual open-only replay: completing each owner intentionally produces separate episodes. This is not title similarity dedup, historical repair, policy relaxation, or permission to reopen a completed owner.

## plugin?

No: source-ledger selection and transactional successor creation are core scheduler behavior.
