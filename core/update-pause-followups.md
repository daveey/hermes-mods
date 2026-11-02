# Bounded update pauses and pending clarification

Patches: 1135 1143 1608 1609.

- `patches/1135-fix-update-pause-a-paused-turn-waits-on-the-apply-ne.patch`
- `patches/1143-fix-update-pause-withdraw-a-blocked-clarify-so-the-t.patch`
- `patches/1608-daveey-fix-update-pause-a-cooperative-pause-restarts.patch`
- `patches/1609-test-update-pause-reuse-base-fixtures-by-assignment-.patch`

## Behavior

- A capped paused turn waits at most `PARK_MAX_S` (600 seconds) from `paused_at` for a pending drain retry, then continues in place even while the drainer remains alive.
- A turn paused for `STUCK_WARN_S` (900 seconds) logs a warning identifying the session and wait.
- For a turn eligible for automatic continuation after an update, a pending clarify is withdrawn with status `paused_for_update` when a pause is requested. The next step boundary can then pause the turn; its notice tells the agent to re-ask.

- **1608 (pause freshness):** A live turn's age no longer disqualifies it from a cooperative update pause. The configured freshness window must still exceed `UPDATE_MARGIN_S` (600 seconds); other eligibility checks remain.
- **1608:** A `core_update_pause` marker uses `max(started_at, parked_at, paused_at)` for freshness. An old turn freshly paused remains eligible in the startup sweep, in-place continuation and auto-continue scheduler; a crash-interrupted turn still uses `started_at` and can expire.
- **1608:** New turn markers advertise `pause_restarts_freshness: True`, and the pause module exposes `PAUSE_RESTARTS_FRESHNESS = True`. External updater consumption of these flags is untested here.
- **1609 (fixture reuse):** Test-only cleanup reuses `home` and `schedule_env` by assignment from the base test module rather than importing the fixture names, avoiding F811 redefinition diagnostics; no runtime behavior changes. Lint execution is untested in this mirror pass.

## How it works

The turn marker exposes `paused_at`. The retry decision uses elapsed pause time rather than drainer liveness alone. A per-turn `clarify_early` provider checks update-pause eligibility and releases a blocked question before the boundary hook runs.

## Code

- `tui_gateway/update_pause.py`
- `tui_gateway/turn_marker.py`
- `tui_gateway/prompt_turn.py`
- `tools/clarify_early.py`
- `tools/clarify_tool.py`

- **1608:** `tui_gateway/turn_marker.py` updates `freshness_since` and `record_turn_start`; `tui_gateway/update_pause.py` changes `not_resumable_reason` and advertises the capability. Tests change in `tests/tui_gateway/test_update_pause.py` and the new `tests/tui_gateway/test_update_pause_freshness.py`.
- **1609:** `tests/tui_gateway/test_update_pause_freshness.py` imports the base module and assigns `home = base.home` and `schedule_env = base.schedule_env`.

## Tests

Source regression coverage:

- `tests/tui_gateway/test_update_pause_cap.py`
- `tests/tui_gateway/test_update_pause.py`
- `tests/tui_gateway/test_update_pause_clarify.py`

See [mirror-1157-verification.md](mirror-1157-verification.md) for the exact files freshly executed for this publication and their results. Files listed here are source coverage references, not a claim that every file was rerun.

### 1608 / 1609 coverage

Patch **1608**, `tests/tui_gateway/test_update_pause_freshness.py`:

- `test_turn_start_marker_advertises_that_its_pause_restarts_the_clock`
- `test_capability_constant_is_advertised`
- `test_eighty_minute_old_live_turn_accepts_a_pause`
- `test_freshness_since_is_paused_at_after_the_pause` (also checks a later `parked_at`)
- `test_crash_interrupted_marker_keeps_the_started_at_clock`
- `test_startup_sweep_continues_an_old_paused_turn_but_not_an_old_crashed_one`
- `test_in_place_continue_resumes_an_old_paused_turn`
- `test_scheduler_continues_an_old_paused_turn_and_clears_an_old_crashed_one`
- `test_max_pauses_still_caps` (checks the recorded pause count and freshness, not drain cap enforcement)

Patch **1608** also changes `tests/tui_gateway/test_update_pause.py::test_turn_that_cannot_be_auto_continued_is_untouched` to cover `short_window` instead of `stale`. Patch **1609** changes only fixture binding in the freshness test module; it adds no test functions.

Untested:
- Live Desktop drain/restart and external updater consumption of the capability flag or marker field.
- Notice-hold integration and malformed pause timestamp handling have no dedicated new tests in these patches.
- F811 lint execution for 1609 was not performed by this mirror pass.

These were not rerun by the mirror pass.

## Applying

Apply the complete numbered series at the frozen merge-base with `git am -3 --empty=keep`. Earlier patches include a curated integration overlay; individual entries are not promised to install independently. See [the inventory](README.md) and [verification report](mirror-1157-verification.md).

Apply **1608** before its fixture-only follow-up **1609**. No new configuration is added; `desktop.auto_continue.freshness_minutes` still controls the window and must exceed the update margin for pause eligibility.

## Gotchas

This does not promise that every turn can auto-continue. Clarify withdrawal is restricted to eligible turns. No live Desktop drain was exercised for this mirror.

## Plugin?

No. This changes core lifecycle, admission, discovery, routing or its tests/workflows rather than a plugin-owned extension point.
