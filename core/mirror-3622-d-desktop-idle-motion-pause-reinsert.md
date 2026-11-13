# Desktop idle-motion pause re-inserts after stale removal

Patches: **3618**.

## Behavior
- After a reload into a new renderer process while idle motion is paused, the fresh pause stylesheet is no longer deleted by the removal of the stale one when both share the same insertCSS key. The window stays paused for the idle period instead of animating.
- A resume arriving while the re-apply waits on the stale removal still leaves the window unfrozen.

## How it works
- 3618 makes `#remove` return a promise that settles when removal finishes; `noteLoaded` claims the window's slot with a key that inserts only after that settles (so the 1 s paused tick does not stack a second copy). Insertion is factored into `#insert`, shared with `#applyMissing`.

## Code
- `apps/desktop/electron/idle-motion-pause.ts`
- `apps/desktop/electron/idle-motion-pause.test.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/electron/idle-motion-pause.test.ts`: `a reload into a new renderer process while paused keeps the fresh sheet when its key repeats the stale one`, `a resume while a reload re-apply waits on the stale removal still leaves the window unfrozen`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The failure mode before the fix was fail-safe (animation kept running), never a frozen window.
