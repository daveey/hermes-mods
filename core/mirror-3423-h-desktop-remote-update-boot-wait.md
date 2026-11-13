# Desktop boot waits out a remote's own update

Patches: **3399**.

## Behavior
- When a remote Hermes is running its own update and the SSH preflight refuses startup ("SSH startup is paused"), the Desktop renderer no longer parks on the "Hermes couldn't start" overlay. It keeps polling every 5 s outside the bounded boot retry budget, shows a "Waiting for the remote Hermes update to finish…" message, and connects without a click once the update exits.
- A pause longer than the wait cap (30 minutes) still ends on the recovery overlay.
- Other boot failures are unaffected.
- Localized boot strings were added for the new message (several locale files).

## How it works
- 3399 adds `remote-update-wait.ts`, which recognizes the refusal by the phrase all producers share (the error crosses IPC as text, so `kind`/`code` are lost), tracks the wait with a cap and reset, and is wired into `use-gateway-boot.ts`. Main's gate answers polls from its cached refusal without dialing until its own backoff opens, so each poll costs one IPC round trip.

## Code
- `apps/desktop/src/app/gateway/remote-update-wait.ts`
- `apps/desktop/src/app/gateway/hooks/use-gateway-boot.ts`
- `apps/desktop/src/i18n/*_boot.ts` (boot strings)

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/src/app/gateway/hooks/use-gateway-boot-remote-update.test.tsx`: `INITIAL boot during a remote update keeps waiting past the bounded retry budget, then connects without a click once the update exits`, `a remote update that pauses SSH startup past the wait cap still ends in the recovery overlay`.
- `apps/desktop/src/app/gateway/remote-update-wait.test.ts`: `recognises the refusal as it crosses IPC, and the gate's cached form`, `ignores other boot failures`, `polls until the cap, then gives up; reset starts a fresh wait`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Detection is by message text, so a producer that changes the refusal phrase would silently disable the wait; the source patch adds a test that the known producers still use the matched phrase.
