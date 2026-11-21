# Transaction-owned creation priority receipts

Mirror entries: 5048, 5049, 5050.

## Behavior

- Opt-in fairness recording can retain original tool priority intent: explicit values including zero/null, omission, the observed effective parent priority, and the stored child priority after lane policy.
- Cross-board creator identity survives even when the target board has no local creator row; effective parent priority remains unknown in that case.
- Receipt rows roll back with child creation or a savepoint. Missing/mismatched capture custody invalidates the recorder rather than refusing otherwise valid creation.

## How it works

`inherit_tool_priority()` observes intent before normalization and passes an internal receipt separately from courier/origin creation arguments. `stage()` binds that receipt to the active profile/board capture and creator, reads the stored child result, then appends a bounded `creation` payload using fixed journal SQL inside the existing transaction. Export drains committed journal rows and accounts for their sequence positions.

The fast gate avoids provenance reads when no recorder is active. Courier/direct producers without authoritative original intent are marked unknown; creator episode, parent snapshot cut and producer source hash are not invented. If an observation error caused SQLite to auto-roll back the creation transaction, the error propagates to prevent later writes from committing as orphans.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_creation.py`
- `hermes_cli/kanban_fairness_creation.py`
- `hermes_cli/kanban_fairness_runtime.py`
- `hermes_cli/kanban_fairness_store.py`
- `tools/kanban_tools.py`
- `website/docs/user-guide/configuration.md`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Creation tests cover explicit/omitted intent, donation cap, unknown provenance, lane-adjusted results, named-board binding, missing journal/custody, oversized scalars, bounded loss, abandoned capture, outer/savepoint rollback and SQLite auto-rollback. Tool tests cover original intent and absent-local-creator lineage, plus courier exclusion of internal observation data. Direct producer behavior is characterized as unknown intent. REST and separately invoked terminal/execute-code entry parity are untested here.

Source test/coverage paths:

- `tests/hermes_cli/test_kanban_fairness_creation.py`
- `tests/tools/test_kanban_create_priority_inheritance.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

A receipt is an observation, not a coverage certificate or a new priority policy. Unknown parent priority is not a known zero. Auto-rollback is the exception to observation failure being non-fatal: continuing an already-aborted creation transaction would corrupt custody.

## Why core instead of a plugin

Original tool intent and post-policy stored results meet inside core creation transactions. A post-tool plugin cannot reconstruct omitted arguments or make its evidence share SQLite rollback custody.
