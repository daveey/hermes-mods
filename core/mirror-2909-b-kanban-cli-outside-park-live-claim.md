# Kanban CLI: schedule/block from outside refuses a live worker's card

Patches: 2812.

- **2812** (`45a36af491`, fix(kanban): CLI schedule/block from outside refuses a card a live worker owns; pin live_claim own-holder and primary gates)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- The CLI path passed `expected_run_id=None` for an outside caller and ended the live worker's open run. `_guarded_park` now asks `tools.kanban_tools_live_park.live_claim()` per id when the caller does not own the run, refusing with the tool's text (holder, run, "Nothing changed", comment/reclaim/archive guidance). Bulk `--ids` refuses only live ids, parks the rest and exits 1. `kb.schedule_task/block_task` are unchanged.
- Also pins the `live_claim()` guards for own-holder and non-primary board.

## How it works

- `hermes_cli/kanban_cli_park.py`, `tools/kanban_tools_live_park.py`.

## Code

- `hermes_cli/kanban_cli_park.py`
- `tools/kanban_tools_live_park.py`

## Tests

- `tests/hermes_cli/test_kanban_cli_outside_park_live_claim.py` (live-claim schedule/block refused and run stays open; bulk refuses only live; owner still parks; gone worker and ready card still park; tool and CLI refusal text match) and `test_kanban_outside_park_board_claim.py`.

Test files touched by these patches:

- `tests/hermes_cli/test_kanban_cli_outside_park_live_claim.py`
- `tests/hermes_cli/test_kanban_outside_park_board_claim.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Internal parkers keep their behavior; only the CLI outside-caller path changed.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
