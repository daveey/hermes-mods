# Capped turns parked for a core update keep continuation eligibility

Patches:

- `patches/0962-fix-core-update-checkpoint-capped-turns-without-retr.patch`

A capped turn parked between core-update drain windows was aged from `started_at`, so under the default 15 min freshness a longer park dropped its continuation marker.

## Behavior

The live writer restamps `parked_at` while parking (and once as parking ends); the in-place continue, the startup sweep and the notice hold measure `core_update_pause` freshness through `turn_marker.freshness_since`.

## How it works

`update_pause.py` restamps; `session_auto_continue.py` and `startup_resume.py` read the new freshness.

## Code

`tui_gateway/update_pause.py`, `turn_marker.py`, `session_auto_continue.py`, `startup_resume.py`.

## Tests

`tests/tui_gateway/test_update_pause_cap.py` (new), `test_update_pause.py`.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.
