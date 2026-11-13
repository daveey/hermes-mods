# Hub admission: retired hub-write needs

Patches: **2987**.

## Behavior
- The hub admission rubric no longer admits two hub-write needs (the board admin key and the instance role); a judge reply naming either is denied, and comment-relay cards naming them are no longer exempt.

## How it works
2987 removes both ids from `NEEDS` and from the comment-relay hub-context pattern, and updates tool schema text.


## Code
- `hermes_cli/kanban_comment_relay.py`
- `hermes_cli/kanban_hub_admission.py`
- `tools/kanban_tools_schemas.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/test_kanban_comment_relay.py`: `test_retired_hub_write_needs_do_not_exempt_a_relay`.
- `tests/test_kanban_hub_admission.py`: `test_retired_hub_write_needs_are_not_admitted`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
Cards that rely on these needs must be treated as hub-write work.
