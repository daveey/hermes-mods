# A headless wake leaves a turn paused for an update to the restarting backend

Patches: **2650–2653**.

## Behavior

- When a core update pauses a chat's turn (marker reason `update_pause`) and the backend that paused it restarts, that backend's startup sweep continues the turn where the chat's viewers attach. Before this change a headless process (the messaging gateway's kanban sweeper) could wake the same chat for a waiting notice a few seconds after the pause, cold-resume it, and so schedule the paused turn's continuation in a process no Desktop window is attached to. The continuation re-asked an open clarify form there, nobody saw it, and the user's next message took the chat over and cancelled it.
- Now `_wake_session_turn` returns `"busy"` without resuming an unloaded chat while that chat carries a fresh core-update pause whose writer process is gone. Callers treat `"busy"` as before: the sweeper writes the passive notice row, an automation replay rechecks later. One INFO line says the chat is left to its backend's continuation.
- "Fresh" means younger than `SUCCESSOR_YIELD_S` (equal to `KICKOFF_HOLD_S`, the bound the continuation kickoff already has). Past that window, with a live writer, with another marker reason, or with `auto_continue` off, nothing changes and any reader continues the turn.
- The lookup covers the launch home and each profile the wake serves. A directory under `profiles/` whose name is not a valid profile id is skipped; it no longer makes every headless wake of an unloaded chat return `"error"`. The lookup does not register profiles as served by this process or activate multi-profile hosting.
- 2652 and 2653 change no behavior: a test stops depending on a fixed pid, and the lookup is inlined to keep a file at its line cap.

## How it works

- 2650: `update_pause.successor_owes_continuation(home, session_key)` reads the durable turn marker. It is true when the marker's reason is the update pause, `auto_continue` is not false, `marker_writer_state` is not `alive`, and `0 <= now - freshness_since(marker) < SUCCESSOR_YIELD_S`. `_wake_session_turn`, in the branch where no live session exists, checks it before calling `session.resume`.
- 2651: new `update_pause.paused_turn_homes(launch_home, profiles)` returns the launch home plus each named profile's directory, resolved read-only through `hermes_cli.profiles.get_profile_dir`; a name it refuses (`ValueError`) is skipped. This replaced resolving through `server._profile_home`, which raises for invalid names and has the side effect of registering the profile as served.
- 2652: the profile-home test stubs `marker_writer_state` to `"dead"` instead of writing a fixed pid, which on some hosts could be a live process.
- 2653: the `paused_turn_homes(...)` expression is inlined into the `any(...)` in `_wake_session_turn` so `session_notifications.py` stays at its 2000-line cap.

## Code

`tui_gateway/update_pause.py` (`SUCCESSOR_YIELD_S`, `successor_owes_continuation`, `paused_turn_homes`), `tui_gateway/session_notifications.py` (`_wake_session_turn`).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tui_gateway/test_wake_yields_paused_turn.py` (built on the `recovery` fixture of `tests/tui_gateway/test_update_pause_recovery.py`). Entry path: `server._wake_session_turn` over a real `session.resume` wiring and a dead writer's durable pause, plus `update_pause.successor_owes_continuation` directly. Covers: a fresh pause returns `busy`, loads nothing, leaves the marker unchanged and logs; a pause older than the window is continued; marker reason, `auto_continue` false and a different chat key owe nothing; a live writer owes nothing; a non-conforming `profiles/` entry does not fail the wake; a fresh pause in a named profile's home returns `busy` with no profile registered or hosting activated.

**Untested**: the actual restarting backend's startup sweep picking up the turn, and the real kanban sweeper and automation-replay callers of `_wake_session_turn` (the `busy` handling there is existing behavior).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- If the restarting backend never comes back within `SUCCESSOR_YIELD_S`, the next wake after the window continues the turn in the waking process, as before; notices in the meantime are only rows.
- A stale marker whose writer pid is reused by an unrelated live process reads as `alive` and is not yielded; that falls back to the previous behavior.
- Profile directories that fail the name check are not searched for markers.

## Plugin

Core change; no plugin required.
