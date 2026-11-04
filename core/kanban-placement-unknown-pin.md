# Kanban placement: unknown pin names are refused as unknown

Patches: **1818** (`581a964958`, `[daveey] fix(kanban): refuse a placement pin hermes-hub has no record of as an unknown name, listing the gateways it knows`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- A `placement` pin naming no gateway hermes-hub has a record of is refused as an unknown name, listing the gateways it knows, instead of "not live: capability record stale or missing".

## How it works

`hermes_cli/kanban_placement.canonical_pin`/`target_live`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1818: Problem: kanban_create(placement='hub-ec2') was refused 22 times in 5 sessions (10-28..11-02; 17 of them in one MacBook chat on 11-02) with "hub-ec2 not live: hub-ec2's capability record is stale or missing". 'mac-mini' was refused the same way. Every caller dropped the pin and let the default route choose.

## Code

`hermes_cli/kanban_placement.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_placement.py`: `test_desktop_gateway_name_through_the_tool_is_refused_with_the_names_to_use`, `test_desktop_gateway_name_with_a_configured_alias_creates_on_the_hub`, `test_target_live_names_the_known_gateways_for_a_name_it_has_no_record_of`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
