# Paused-turn activity follows configured continuation bounds

Patches: 3806.

## Behavior

A paused TUI/Desktop turn counts as live only while that home's configured auto-continuation policy could still continue it. Disabled, too-old or exhausted pauses stop counting.

## How it works

`turn_marker.auto_continue_bounds` becomes the shared parser for enabled, freshness and attempt settings. The producer delegates to it; the activity reader loads each home's own effective config and lazily reuses its bounds during the scan. Defaults remain enabled, fifteen minutes and two attempts, with established coercion/default handling. The gateway checkpoint reader's separate one-hour limit remains its own fail-closed cap, not a claim to mirror a producer setting.

## Code

- `hermes_cli/turn_session_activity.py`
- `tui_gateway/session_auto_continue.py`
- `tui_gateway/turn_marker.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/hermes_cli/test_turn_session_activity.py` (patch 3806).

- **3806:** Disabled/expired/exhausted pauses do not annotate turn_live; raised valid windows do; profiles use distinct policy; parser tests pin producer/reader defaults and coercions.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

Profiles must use their own config rather than the inspecting process's config. Unreadable config falls back to the same defaults as the producer. This changes paused-turn annotation, not a promise to resume every marked pause.

## Plugin?

No. Producer/reader consistency and per-home activity annotation live in core.
