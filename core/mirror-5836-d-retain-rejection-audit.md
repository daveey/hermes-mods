# Separate exact retain refusal audits from replica drift

Source patch ordinals: 5790–5791.

## Behavior

A definitive, independently custody-matched retain refusal for a run closed on both copies remains visible as a command audit, without alone making an otherwise equal replica dirty. Other rejected writes remain differences.

## How it works

`audited_rejection` checks the rejected tasks/retain operation against immutable command/custody and frozen wire evidence, and closed-run evidence on live and shadow copies. `rejected_summary` returns the full rejection count and only the matching audit operation IDs. The diff result keeps `rejected_ops` and exposes `audited_retain_rejections`; ordinary missing/extra/field/child differences still gate clean.

## Code

- `hermes_cli/kanban_sync_retain_drift.py`
- `hermes_cli/kanban_sync_client.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/hermes_cli/test_kanban_sync_retain_drift.py` covers exact diagnostic-only rejection, incomplete/conflicting proof failing closed, retained audit visibility and coexistence with real drift.

## Applying

Apply the helper with its diff integration and subsequent rejection-count refactor. No transport mutation or store migration is required.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

This is read-only classification. It does not retire an outbox operation, confirm retention, alter custody/fences or hide unrelated rejected operations. Full rejection counts include diagnostic-only audits.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
