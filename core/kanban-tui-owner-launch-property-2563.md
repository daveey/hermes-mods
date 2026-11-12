# TUI notice owner reads the launch authority as a property

Patches: **2532**.

## Behavior

`SessionAuthorities.launch` is a property, but the TUI notice owner called it. Every gateway backed by the session-authority registry therefore raised `'SessionAuthority' object is not callable` for each `tui` subscription, and no legacy TUI notice was claimed. Now the property is read, and registry-backed gateways claim and deliver owned TUI rows again. Gateways without a registry still use `runner.session_authority`.

## How it works

`_launch_authority` in `gateway/kanban_tui_owner.py` uses `registry.launch` instead of `registry.launch()`; the profile matching that follows is unchanged. See [kanban-tui-legacy-notice-fifo-2491.md](kanban-tui-legacy-notice-fifo-2491.md).

## Code

`gateway/kanban_tui_owner.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/gateway/test_kanban_tui_owner_delivery.py`, now with a real `SessionAuthorities` registry (the old fixture set `session_authorities=None`, which hid the bug). Covered entry paths: `_launch_authority` for the default, no and another profile, and a gateway tick that claims and delivers an owned TUI row. Live Desktop delivery is untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

No caveats; a one-token fix with no new flags or migration.

## Plugin

Core change; no plugin required.
