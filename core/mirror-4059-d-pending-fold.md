# Pending-fold admission custody boundary tests

Mirror snapshot 4059; covered ordinals: 3943.

## Behavior

Test-only coverage pins which pending notices can be transferred into an admitted head turn and ensures transfer replay or stale generations cannot duplicate text, settle newer transfers, or requeue an already delivered steer.

## How it works

`tests/gateway/test_fold_pending_contracts.py` drives existing `gateway/session_fold_pending.py` with real admission/claim/transfer writes, while preventing the harness from starting agents. It distinguishes an update-resuming human head from an ordinary human head, remote-ack wakes from local completion notices, and optional candidate-scan failure from failure of the admitted turn. Generation fences remain in the actual settlement writes, not just in test fixture maps.

## Code

These ordinals change only the test files listed below; the existing implementation boundaries are identified under How it works.

### Mirrored commits

- 3943: `df18cf1964878061fb04b0e17afd5cda263b5fab` — Pins pending-fold custody, replay idempotency, and generation fences.

## Tests

- `tests/gateway/test_fold_pending_contracts.py`: only resuming human heads fold pending completion. Remote event-ack keys stay queued in their own turn; held wakes do not block a later completion fold. Candidate-scan exceptions preserve head text and rows. Replaying a stale candidate snapshot appends no text and changes no rows. An old fold map cannot requeue a newer generation's delivered steer. Settlement using a stale generation leaves rows untouched; the valid generation then marks completion as steered or requeues on failure.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

These tests establish database custody and text-transfer rules, not successful execution of an agent or remote acknowledgment. Pending folds and explicit delivered steering have different retry/settlement semantics; do not treat them interchangeably.

## Plugin?

No. Transfer ownership and settlement generation fences are core admission invariants.
