# Upstream sync 55b0b2611f: full-suite fixups

Patches: **3651**.

## Behavior
- A multiplexed gateway keeps one routing store in its launch home; a served profile's turn no longer saves its host cwd there, so restarts start it in `terminal.cwd`.
- The empty-response machinery-kind mirror also treats the fork's `kanban_notice` and `loop_tick` kinds as machinery, matching the gateway response filters.
- Several tests are repaired after the merge (duplicate fake adapter methods dropped, context-usage stub gets status-routing fields, profile-create test reads the sandboxed wrapper dir, preswap replay-env test checks behavior instead of source text).

## How it works
- 3651 adds `_store_is_this_profiles()` in `gateway/session_terminal_cwd.py` and gates cwd restore on it; extends `_MACHINERY_DISPLAY_KINDS` in `agent/turn_empty_response.py`; edits the listed tests.

## Code
- `agent/turn_empty_response.py`
- `gateway/session_terminal_cwd.py`
- `tests/gateway/test_api_context_usage.py`
- `tests/gateway/test_profile_create_through_gateway.py`
- `tests/gateway/test_session_race_guard.py`
- `tests/hermes_cli/test_update_preswap_drain.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_update_preswap_drain.py`: `test_the_opt_in_is_not_inherited_by_a_replayed_gateway`.
- The other test files are edited without new test names; the cwd and machinery-kind code changes are untested by a new test.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The patch assumes the upstream `55b0b2611f` merge bridge (`bridges/upstream-55b0b2611f-via-db5a056366.diff`) is already applied; in series order that bridge precedes 3651.
