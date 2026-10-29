# fix(kanban): placement=<live gateway> reaches any gateway, not only the routed target; refusal message updated [daveey]

Patch 0945, source `0bc5626cfeb13aac75eed2c12793680cba8ad4c5`.

## Behavior

fix(kanban): placement=<live gateway> reaches any gateway, not only the routed target; refusal message updated [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _decide(args: dict, *, origin_intent=None, now: Optional[float] = None, cfg:`
- `class MachinePinRefused(Exception):  # not ValueError: label-syntax handlers mus`
- `def test_caller_pin_wins_both_ways():`
- `def test_configured_alias_maps_any_name_and_unknown_names_still_refuse():`
- `def test_heavy_route_machine_check_reads_the_board_registry(monkeypatch, entry,`
- `KANBAN_CREATE_SCHEMA = _schema(`

## Code

- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_worker_node.py`
- `tools/kanban_tools_schemas.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_placement.py`

New or changed test definitions present in the source diff:

- `test_pin_to_a_live_gateway_without_a_route_entry_goes_there`
- `test_pin_to_a_non_routed_gateway_goes_there_even_with_a_route_of_its_own`
- `test_pin_without_any_route_config_goes_to_a_live_gateway`
- `test_pin_to_an_unknown_or_not_live_gateway_is_still_refused`
- `test_foreign_pin_keeps_the_structural_refusals`
- `test_foreign_pin_with_parents_is_deferred_to_the_pinned_gateway`
- `test_foreign_pin_for_an_origin_bound_card_is_not_forwarded`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
