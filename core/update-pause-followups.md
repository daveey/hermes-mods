# Bounded update pauses and pending clarification

Patches: 1135 1143.

## Behavior

- A capped paused turn waits at most `PARK_MAX_S` (600 seconds) from `paused_at` for a pending drain retry, then continues in place even while the drainer remains alive.
- A turn paused for `STUCK_WARN_S` (900 seconds) logs a warning identifying the session and wait.
- For a turn eligible for automatic continuation after an update, a pending clarify is withdrawn with status `paused_for_update` when a pause is requested. The next step boundary can then pause the turn; its notice tells the agent to re-ask.

## How it works

The turn marker exposes `paused_at`. The retry decision uses elapsed pause time rather than drainer liveness alone. A per-turn `clarify_early` provider checks update-pause eligibility and releases a blocked question before the boundary hook runs.

## Code

- `tui_gateway/update_pause.py`
- `tui_gateway/turn_marker.py`
- `tui_gateway/prompt_turn.py`
- `tools/clarify_early.py`
- `tools/clarify_tool.py`

## Tests

Source regression coverage:

- `tests/tui_gateway/test_update_pause_cap.py`
- `tests/tui_gateway/test_update_pause.py`
- `tests/tui_gateway/test_update_pause_clarify.py`

See [mirror-1157-verification.md](mirror-1157-verification.md) for the exact files freshly executed for this publication and their results. Files listed here are source coverage references, not a claim that every file was rerun.

## Applying

Apply the complete numbered series at the frozen merge-base with `git am -3 --empty=keep`. Earlier patches include a curated integration overlay; individual entries are not promised to install independently. See [the inventory](README.md) and [verification report](mirror-1157-verification.md).

## Gotchas

This does not promise that every turn can auto-continue. Clarify withdrawal is restricted to eligible turns. No live Desktop drain was exercised for this mirror.

## Plugin?

No. This changes core lifecycle, admission, discovery, routing or its tests/workflows rather than a plugin-owned extension point.
