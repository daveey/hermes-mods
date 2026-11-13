# Kanban placement: home-relative state paths

Patches: **3716**.

## Behavior

- Card bodies that reference Hermes state through `$HOME/.hermes/...`, `${HOME}/.hermes/...` (including double-quoted forms), or a relative `.hermes/<x>` after a `cd` to home (`cd`, `cd ~`, `cd $HOME`, `cd /home/<user>`, quoted forms) now count as state-path markers, so the card is pinned to the home gateway rather than placed anywhere.
- A later `cd` elsewhere is not tracked, so such text still pins (fails toward the home gateway).
- Code-checkout exclusions (agent checkout, repos, bin) are unchanged.
- A declared `hub:read` does not clear the new forms; only `~` and `/home/<user>` paths are cleared as before.
- A pool worker still keeps a card naming its own `$HOME/.hermes/kanban/workspaces/<task>` artifact (covered for `~`, `$HOME`, `${HOME}`).
- This is a placement fix only; hub-read still enforces its own read roots.

## How it works

3716 extends `kanban_placement.HOME_PREFIX` with `$HOME`, `${HOME}` and quoted forms, extends `HERMES_STATE_PATH` to match relative `.hermes/<x>` after an earlier `cd` to home, and makes `kanban_home_marker_placement._state_path` use `HOME_PREFIX`.

## Code

- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_home_marker_placement.py`
- `tests/hermes_cli/test_kanban_state_path_home_forms.py`
- `tests/hermes_cli/test_kanban_pool_queue.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_state_path_home_forms.py`: `test_home_forms_are_state_paths`, `test_non_state_forms_stay_portable`, `test_declared_hub_read_does_not_clear_home_forms`, `test_tilde_root_path_still_clears`.
- `tests/hermes_cli/test_kanban_pool_queue.py`: `test_a_worker_keeps_a_card_naming_its_own_artifact` (parametrized over `~`, `$HOME`, `${HOME}`).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas

- Matching is textual; a `cd` away from home after the first `cd` is not tracked.
