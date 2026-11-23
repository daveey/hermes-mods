# Kanban: durable refusal stop before reclaim retry (mirror 5655–5657)

## Behavior
- An attributable provider refusal is recorded as a durable stop before any reclaim-retry can fire.
- If publishing the decision notice fails, the stop is retained; a notification outage cannot roll back the stop or other dead-worker progress.
- No new execution allowance is issued; the prior supervisor route is reused.

## How it works
`kanban_refusal_receipt.py` and `kanban_refusal_proof.py` write the receipt; `kanban_db_dispatch.py`/`kanban_db_promote.py` consult it; `kanban_spend_decision.py`/`kanban_spend_promote.py` publish via the existing safe publisher inside reclaim. Restricted episodes without a uid resolve by id fallback. Audit tests inventory source-receipt sites and the ready-entry registry.

## Code
- `hermes_cli/kanban_refusal_receipt.py`
- `hermes_cli/kanban_refusal_proof.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_promote.py`
- `hermes_cli/kanban_spend_decision.py`
- `hermes_cli/kanban_spend_promote.py`
- `gateway/session_kanban.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/gateway/test_kanban_refusal_receipt.py::test_typed_refusal_survives_the_actual_worker_writer` checks typed fields without provider prose. `tests/hermes_cli/test_kanban_refusal_stop.py::test_refused_interpreter_stops_once_without_retry` checks local triage and ended run; `test_primary_refusal_queues_exact_release_without_local_closure` checks that primary closure awaits the canonical side. `test_kanban_refusal_recovery.py::test_failed_notice_keeps_stop_and_other_worker_progress` checks notification failure containment. Source-site/ready-entry registry tests are inventories, not tests of every runtime entry path; paths not exercised by the named cases remain untested.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Builds on the refusal classification doc (5644–5649).
- New ready-entry sites must be added to the source-receipt inventory tests.

## plugin?
No. Core change.
