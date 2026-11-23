# Kanban: PR acceptance falls back to REST on GraphQL exhaustion (mirror 5705)

## Behavior
- When GitHub GraphQL quota is exhausted, PR acceptance falls back to REST only on quota errors rather than stranding delivered PR tasks.
- Profile credentials, required context/app identities, complete exact-head evidence, merge-time cutoff and fresh head/status are retained.

## How it works
`kanban_pr_acceptance.py` catches quota errors and delegates to `kanban_pr_rest.py`.

## Code
- `hermes_cli/kanban_pr_acceptance.py`
- `hermes_cli/kanban_pr_rest.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_kanban_pr_rest.py::test_exhausted_completion_uses_rest` exercises acceptance/completion with fixture GitHub responses and exact-head failure controls. `test_transport_classifies_rate_limit_before_http_auth` checks quota-vs-auth classification; `test_rest_policy_unreadable_does_not_accept` checks fail-closed policy reads. Live GitHub REST/GraphQL requests are untested.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Non-quota GraphQL errors do not fall back.

## plugin?
No. Core change.
