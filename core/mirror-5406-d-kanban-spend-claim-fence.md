# Kanban spend limits: fence claims on every executor

Mirror snapshot 5406; ordinals 5395, 5396, 5397, 5398.

Entries:

- **5395**: fix(kanban): fence spend-limited claims on every executor
- **5396**: fix(kanban): refuse spend claims on config backend outage
- **5397**: Allow recorded spend release from scheduled cards
- **5398**: Keep promotion SQL explicitly off running custody

## Behavior

- **5395**: a read-only predicate before lease acquisition plus a recheck in the claim transaction.
- **5396**: a config backend outage refuses the claim instead of crashing dispatch.
- **5397/5398**: recorded spend release from scheduled cards with an exact status CAS and an explicit off-running allowlist.

## How it works

- `kanban_spend_claim.py`, `kanban_db.py`, `kanban_db_promote.py`.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_spend_claim.py`
- `hermes_cli/kanban_db_promote.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_cli/test_kanban_spend_claim.py`
- `tests/hermes_cli/test_kanban_spend_claim_accounting.py`
- `tests/hermes_cli/test_kanban_blocker_input_config.py`
- `tests/hermes_cli/test_kanban_deploy_window.py`
- `tests/hermes_cli/test_kanban_spend_budget_hold.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Budgets and priorities are unchanged.

## Why core instead of a plugin

Claim arbitration is core.
