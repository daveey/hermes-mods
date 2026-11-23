# Kanban: adopt local decision ownership from an interactive chat (mirror 5662–5663)

## Behavior
- `origin-adopt TASK --local` lets the current interactive chat become the decision owner of a local card with no conflicting responsible chat, whether or not the current chat already has an observer subscription.
- Worker, delegated, machine and unknown contexts are refused, as is conflicting existing ownership (checked inside the write transaction).
- An existing exact observer row is upgraded to origin without changing its routing, delivery mode or notify cursor; if absent, a new origin subscription is inserted at the current event cursor. Card task fields are unchanged.
- Cross-profile adoption keeps using the courier route; guidance never says to switch the chat into the card's profile.

## How it works
`kanban_local_adoption.py` validates fresh local interactive provenance and performs the upgrade; `kanban_origin.py`/`kanban_origin_owner.py` and the parser expose it; courier-only refusal text names this path.

## Code
- `hermes_cli/kanban_local_adoption.py`
- `hermes_cli/kanban_origin.py`
- `hermes_cli/kanban_origin_owner.py`
- `hermes_cli/kanban_parser.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_kanban_local_adoption.py` exercises the command/parser, worker and unknown-source refusals, cursor/routing preservation, conflicting creators, and creation of a missing subscription. `tests/hermes_cli/test_kanban_origin_adoption.py::test_cross_profile_hint_executes_through_courier_owner_entry` follows the generated cross-profile route with fixture transport. A live courier deployment and interactive tool subprocess are untested by these cases.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Local provenance is profile-local.

## plugin?
No. Core change.
