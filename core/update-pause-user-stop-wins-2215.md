# A user Stop beats the core-update pause

Patches:

- `patches/2198-daveey-A-user-Stop-beats-the-core-update-pause-of-th.patch`

A Stop that landed while the update-pause hook ran ended the turn as stopped, but the hook had stamped the interrupted-turn marker `core_update_pause`, so the restarted backend continued a turn the user had stopped.

## Behavior

- `boundary_pause_hook` declines (under `history_lock`) when a user interrupt is pending on the agent; a backend-exit or lease-takeover interrupt is not a user Stop.
- `session.interrupt` drops the running turn's paid pause (`user_stop_wins`) before retiring the marker.
- The agent loop ends the turn as interrupted, without another model call, when a user Stop is pending after the hook.
- The turn's end announces/keeps the pause only while it still stands (`pause_stands`).
- Unchanged: a pause without Stop resumes; an idle Stop after the turn ended paused keeps the paused request; a backend exit keeps the marker.

## How it works

The decision lives in `agent/update_pause_cancel.py` (`user_stop_pending`) and `tui_gateway/update_pause.py` (`user_stop_wins`, `pause_stands`, `_drop_pause`); `methods_session_interrupt.py`, `prompt_turn.py` and `turn_iteration_prep.py` call them.

## Code

`agent/update_pause_cancel.py`, `agent/turn_iteration_prep.py`, `tui_gateway/update_pause.py`, `tui_gateway/methods_session_interrupt.py`, `tui_gateway/prompt_turn.py`.

## Tests

Tests are the coverage supplied with the patches. They were **not executed in this mirror**.

`tests/tui_gateway/test_update_pause_user_stop.py`: a Stop inside or right after the hook, on the boundary and mid-tool paths, is not continued after restart (through the real `session.interrupt` RPC); a no-Stop control resumes; an idle Stop; unit tests for the hook, the drop and marker identity.

## Applying

Apply the listed patches in order as part of the full ordered replay described in [README.md](README.md) (`git am -3 --empty=keep`); no extra configuration unless named above.

## Gotchas

A Stop never drops another turn's marker. Only the tui_gateway RPC Stop path is covered; other front-ends are untested.

## Plugin

None.
