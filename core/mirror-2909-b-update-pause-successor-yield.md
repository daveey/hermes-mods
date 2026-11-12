# Update pause: successor ownership follows the startup sweep

Patches: 2805.

- **2805** (`23cd22828e`, A paused turn the startup sweep will not take is owed to no successor; automation replays recheck past the yield window)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- `successor_owes_continuation` reported a dead writer's pause as owed for the first yield window of every new process regardless of age, delaying headless deliveries after restart. It now returns False when the startup sweep would skip the marker (`startup_resume._skip_reason` with the sweep's own settings) or the sweep is off, so they cannot drift.
- `automation_replay` rechecked a busy wake 10 x 60 s, shorter than `SUCCESSOR_YIELD_UNKNOWN_S` (660 s); `BUSY_RECHECKS_MAX` is now derived from the window plus one interval (12).

## How it works

- `tui_gateway/update_pause.py`, `tui_gateway/automation_replay.py`.

## Code

- `tui_gateway/automation_replay.py`
- `tui_gateway/update_pause.py`

## Tests

- `tests/tui_gateway/test_cold_resume_yields_paused_turn.py` (stale or out-of-attempts pause not owed; sweep-taken pause owed; busy replay still pending at window end) and `test_wake_yields_paused_turn.py` (probe patched where the sweep reads it).

Test files touched by these patches:

- `tests/tui_gateway/test_cold_resume_yields_paused_turn.py`
- `tests/tui_gateway/test_wake_yields_paused_turn.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
