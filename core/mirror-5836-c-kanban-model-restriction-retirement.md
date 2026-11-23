# Kanban: retire stored model restrictions in owner dispatch (mirror 5652–5654)

## Behavior
- Owner dispatch clears legacy stored model selectors and mandatory flags by exact-value compare-and-swap, with an audit record and standard sync field clocks.
- Refused body lines become reversible historical JSON evidence through supported body editing.
- Live run, claim and session custody are untouched.
- Retained pin-author diagnostics and downstream consumers keep reading prior policy evidence (from the audit, not task columns) under the existing conservative first-epoch rules.

## How it works
`kanban_model_retirement.py` performs bounded rotation inside `kanban_dispatch_owner.py`; `kanban_db_edit.py` provides the body edit. Identity-incomplete synced stores fail closed and keyset ranges replace sorting a computed cursor key. `kanban_review_model.py` reads retired-author provenance.

## Code
- `hermes_cli/kanban_model_retirement.py`
- `hermes_cli/kanban_dispatch_owner.py`
- `hermes_cli/kanban_db_edit.py`
- `hermes_cli/kanban_review_model.py`
- `website/docs/user-guide/configuration.md`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_kanban_model_retirement.py`: `test_each_exact_selector_cas_atom`, `test_second_connection_wins_at_transaction_boundary`, `test_running_claim_run_and_session_are_untouched`, and `test_real_dispatch_once_retires_even_without_spawn_budget` exercise the CAS and owner dispatch. `test_kanban_model_retirement_sync.py::test_policy_field_clocks_and_replica_readback` checks field clocks and replica data; `test_kanban_retired_author_provenance.py::test_latest_retirement_does_not_accumulate_old_pins` checks conservative provenance. No interactive CLI/UI entry path is established by these cases (untested).

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Retirement is bounded per dispatch pass; it does not touch routing, admission or custody.

## plugin?
No. Core change.
