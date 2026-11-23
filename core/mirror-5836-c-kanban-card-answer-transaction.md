# Kanban: fenced transaction for remote card answers (mirror 5667–5678, 5717)

## Behavior
- A remote Desktop answer to a card ask is recorded through a fenced owner transaction: independently verified source operation evidence, the current generation fenced in the core transition, event-backed idempotency receipts.
- Remote 'Other' answers are recorded without releasing card holds. Duplicate remote answers are fenced before comment insertion; whitespace operation ids are rejected before writes; a committed answer is recovered after a verification race.
- The authenticated runtime reads the enrolled courier key (UTF-8) and separates install-store owner from courier identity; authenticated HTTPS reads send a fixed generic User-Agent.

## How it works
`kanban_card_answer.py` is the transaction; `_proof.py`, `_source.py` validate provenance; `_runtime.py` and `_transport.py` adapt authenticated runtime/courier calls; `kanban_reason_comment.py` and `kanban_db_comment_ids.py` support comment dedupe.

## Code
- `hermes_cli/kanban_card_answer.py`
- `hermes_cli/kanban_card_answer_proof.py`
- `hermes_cli/kanban_card_answer_source.py`
- `hermes_cli/kanban_card_answer_runtime.py`
- `hermes_cli/kanban_card_answer_transport.py`
- `hermes_cli/kanban_reason_comment.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_kanban_card_answer.py` checks atomic answer/replay, generation fencing and held transitions; `test_kanban_card_answer_other.py::test_informational_answer_is_atomic_idempotent_and_keeps_human_ask` checks comment-only answers. `test_kanban_card_answer_review.py::test_duplicate_between_preflight_and_write_lock_returns_receipt` and `test_kanban_card_answer_verify_race.py::test_duplicate_commits_during_source_read_returns_winner` cover duplicate races. `test_kanban_card_answer_transport.py::test_reads_exact_authenticated_record` inspects the constructed GET/auth/User-Agent with mocked networking. `test_card_answer_courier_integration.py::test_end_to_end_receipt_idempotence_and_readback` uses fixture courier/runtime adapters and SQLite. Live Desktop interaction and an actual HTTPS edge are untested.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Unexpected exceptions keep the module's internal_error envelope.
- The transport test inspects the fixed User-Agent header; it does not reproduce or diagnose a live HTTPS-edge 403.

## plugin?
No. Core change.
