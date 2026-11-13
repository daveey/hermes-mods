# Kanban placement: malformed registry labels fail closed

Patches: **3492**.

## Behavior
- A machine-registry entry whose `labels` is not a list of strings (an int, a bare string, a dict, a tuple, a list containing non-strings) no longer crashes or mis-matches; the entry simply does not qualify.
- A missing, null or empty `labels` stays an empty label set, so a named machine without labels still picks up work.
- Well-formed labels qualify as before.

## How it works
3492 changes `label_machines` to judge labels with the coordination module's `_carries` helper instead of `wanted <= set(entry.get("labels") or [])`. The old expression raised TypeError on non-iterable values and matched the characters of a string. Because the named-machine branch of `_machine_picks_up` also goes through `label_machines`, one malformed entry could previously break named heavy-route routing as well as pool/label-set liveness.

## Code
- `hermes_cli/kanban_placement.py`
- `tests/hermes_cli/test_kanban_placement_labels_shape.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_placement_labels_shape.py`: `test_malformed_registry_labels_fail_closed`, `test_missing_or_empty_labels_are_an_empty_set`, `test_well_formed_labels_still_qualify`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
