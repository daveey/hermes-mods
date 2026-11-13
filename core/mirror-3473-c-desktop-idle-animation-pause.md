# Desktop: pause CSS animations while the system is idle

Patches: **3464**.

## Behavior
- After 300 s with no keyboard or mouse input, every Desktop window gets its CSS animations paused, so an unwatched but visible window (for example one spinner on a display that never sleeps) stops forcing the compositor to redraw at refresh rate.
- Any input removes the pause within about a second.
- Windows that load, reload or open while paused get the pause re-applied. Transitions and JS-driven updates (streamed text) are untouched.
- If the idle time cannot be read, animations are never paused.

## How it works
3464 adds `electron/idle-motion-pause.ts`. It polls `powerMonitor.getSystemIdleTime()` every 30 s while active and every 1 s while paused. At the threshold it `insertCSS`es `*, *::before, *::after { animation-play-state: paused !important; }` into every window, and removes it on input. It re-applies on `did-finish-load`, skips destroyed windows, and survives `insertCSS` failures. `main.ts` wires it in with one line.

## Code
- `apps/desktop/electron/idle-motion-pause.ts`
- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/idle-motion-pause.test.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/electron/idle-motion-pause.test.ts`: `leaves animations alone while the user is active`, `pauses every window once the system is idle, and resumes on input`, `re-applies to a window that reloads or opens while paused`, `skips destroyed windows and survives insertCSS failures`, `never pauses when the idle time cannot be read`, `polls slowly while active, every second while paused, and re-applies on did-finish-load`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Only CSS animations are paused; other sources of repaint are not addressed.
- The CPU measurements in the source commit message are not verified by tests.
