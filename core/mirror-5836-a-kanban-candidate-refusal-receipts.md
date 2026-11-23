# Kanban dispatcher candidate-refusal receipts (mirror 5444–5446)

## Behavior
- Each non-dry dispatcher/pickup pass records bounded receipts of the first refusal reached for unclaimed `ready` cards, observing existing pickup/claim gates and already-computed rank scores without re-ranking. Successful claims suppress earlier observations from the same pass; review-status cards are not recorded.
- Machine-local receipt and pass tables (outside task events and the sync feed) are trimmed after each successful non-dry pass: 7 days, 20,000 receipt rows, 2,000 pass rows; at most 512 refused candidates and 512 score samples per pass, with excess counted in `passes.dropped`. Detail strings are capped at 400 characters. Persistence errors only log a warning.
- A read-only JSON export (`python -m hermes_cli.kanban_candidate_receipts --db ... [--task-id] [--limit]`, opened `mode=ro`; `read()` caps the limit at 20,000) returns version-1 `receipts` and `passes` arrays, newest first. A missing score is `not_observed`, never a recomputed one.
- Pickup skip replay passes the observed card status (`ready`/`review`) through to the recorder; because only `ready` is recorded, review cards and cards with an unknown status produce no receipt rather than a guessed one.
- The receipt reader guards its board connection.

## How it works
`hermes_cli/kanban_candidate_receipts.py` holds storage and export. Small hooks in dispatch (`kanban_db_dispatch.py`, `kanban_db_dispatch_preclaim.py`, `kanban_dispatch_priority.py`), hub admission claim, `kanban_pickup_gate.py` and `kanban_worker_node.py` feed it. The worker-node pickup evaluation loop carries the observed candidate status through to the final replay. See `docs/kanban-candidate-receipts.md` in the source.

## Code
`hermes_cli/kanban_candidate_receipts.py`, `kanban_db_dispatch.py`, `kanban_db_dispatch_preclaim.py`, `kanban_dispatch_priority.py`, `kanban_hub_admission_claim.py`, `kanban_pickup_gate.py`, `kanban_worker_node.py`.

## Tests
Source: `tests/hermes_cli/test_kanban_candidate_receipts.py`, `test_kanban_candidate_receipts_dispatch.py`, `test_kanban_candidate_pickup_receipts.py` (per the test names: replay of unknown shapes, first-gate/score/claim suppression, retention bounds, reader filtering, gate classification from pickup facts, pickup and lane-result classification, score tap not re-ranking, dry-run/write-failure non-intrusion, read-only CLI export, and a pickup test parametrized over ready/review status and prefilter/import stage in which review yields zero exported rows). Not executed in this mirror pass.

## Applying
Apply in order; the third patch depends on the worker-node hook from the first.

## Gotchas
Receipts are evidence only: a missing receipt or score is unknown, not proof of eligibility, and pass metadata may age out before its receipts. SQLite reuses freed pages, so retention bounds logical rows, not file size. The source tests use fixtures and temporary databases, not a live deployment.

## plugin?
No.
