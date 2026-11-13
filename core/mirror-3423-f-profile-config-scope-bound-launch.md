# Profile config scope inside a bound launch scope

Patches: **3264, 3286**.

## Behavior
- `profile_config_scope(<launch home>)` inside a bound scope (a scoped turn) binds the launch scope instead of raising `ScopeSwitchRefused` while clearing the terminal policy, so profile provisioning for the launch profile works there.
- Inside a bound launch-home turn, the turn's session, turn id and fence survive; a secondary home inside a bound launch scope gets only its own files.

## How it works
- 3264 changes `launch_profile_policy` to bind the launch scope when already inside a bound scope rather than unbinding with the legacy setters. 3286 adds a test pinning TurnScope preservation (RED against a mutant that rebinds a fresh Scope) and a note on why a snapshot assertion is skipped.

## Code
- `tui_gateway/launch_profile_policy.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_profile_config_scope.py`: `test_the_launch_home_inside_a_bound_scope_binds_the_launch_scope`, `test_a_secondary_home_inside_a_bound_launch_scope_gets_only_its_own_files`, `test_the_launch_home_inside_a_bound_launch_turn_keeps_the_turn`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- None recorded.
