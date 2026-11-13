# Desktop: session writes ride across a hub reconnect

Patches: **3333**.

## Behavior
- Archive, pin and unread writes against a remote hub wait out a reconnect and replay the same intent instead of failing on the first socket hang-up or down-gate fail-fast.
- Definitive refusals (for example `client_upgrade_required`) are not retried; once the reconnect window is spent the failure is returned to the caller.

## How it works
- 3333 adds `session-write-reconnect.ts` (retry honoring the down-gate wait, same identity on replay) and routes `sessions.ts` writes through it.

## Code
- `apps/desktop/src/api/session-write-reconnect.ts`
- `apps/desktop/src/api/sessions.ts`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/src/api/session-write-reconnect.test.ts`: `rides out a hub reconnect and lands the write without a user retry`, `waits at least as long as the down gate says before the next try`, `does not retry a definitive refusal`, `gives the failure back to the caller once the reconnect window is spent`.
- `apps/desktop/src/api/sessions.test.ts`: `replays a hub archive that hit a reconnect with the SAME identity, without a user retry`, `does not replay a definitive refusal (client_upgrade_required)`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
