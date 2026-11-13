# Desktop idle-motion-pause removes a sheet that survives did-finish-load

Patches: **3584**.

## Behavior
- When the idle-motion pause sheet was inserted after a window's document committed but before `did-finish-load`, resume now removes it; previously a second copy was inserted and animations stayed frozen until the next reload.
- A reload whose first `insertCSS` is still pending ends with exactly one sheet, which resume removes.
- Header docs: only top-level BrowserWindow webContents are covered (preview `<webview>` guests keep animating), and an exit animation started during idle without input stays frozen until the next input.

## How it works
3584 makes `noteLoaded` remove the previous key (after its `insertCSS` settles) before re-applying. Removal is factored into `#remove`, shared with `#removeAll`, and a synchronous throw from `removeInsertedCSS` is swallowed like a rejection.

## Code
- `apps/desktop/electron/idle-motion-pause.ts`
- `apps/desktop/electron/idle-motion-pause.test.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/electron/idle-motion-pause.test.ts`: `a window whose sheet survives did-finish-load while paused is unfrozen on resume`, `a reload while paused whose insertCSS is still pending ends with one sheet that resume removes`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
Guest `<webview>` contents are not paused (documented limitation, untested).
