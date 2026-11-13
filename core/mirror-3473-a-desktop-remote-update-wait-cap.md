# Desktop remote-update boot wait: hard cap and shared refusal phrase

Patches: **3424**.

## Behavior
- While a remote gateway refuses connections because an update is paused, Desktop waits up to 30 minutes before showing its overlay. That cap is now hard: a bounded-retry boot failure that hits the same refusal past the cap no longer opens a fresh 30-minute wait, so the overlay cannot be postponed indefinitely.
- The wait resets only on a successful boot, a failure that is not an update refusal, or a connection switch.
- The refusal text is one shared constant used by the producers and the renderer matcher.

## How it works
3424 changes `use-gateway-boot` so the `RemoteUpdateWait` is no longer reset at the cap before the bounded-retry branch. It removes the test-only `waiting` getter from `RemoteUpdateWait` (tests assert the spent-cap behavior through `next()`). It adds `electron/remote-update-refusal.ts` exporting `REMOTE_UPDATE_PAUSED_PHRASE`; `remote-lifecycle`, `windows-remote-lifecycle` and `remote-update-pause` build their refusal from it and the renderer matcher imports it, replacing a test that regex-read electron sources.

## Code
- `apps/desktop/electron/remote-update-refusal.ts`
- `apps/desktop/electron/remote-lifecycle.ts`
- `apps/desktop/electron/windows-remote-lifecycle.ts`
- `apps/desktop/electron/remote-update-pause.ts`
- `apps/desktop/src/app/gateway/hooks/use-gateway-boot.ts`
- `apps/desktop/src/app/gateway/remote-update-wait.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/src/app/gateway/hooks/use-gateway-boot-remote-update.test.tsx` and `apps/desktop/src/app/gateway/remote-update-wait.test.ts` are modified; the patch adds no `test_`-style named cases that could be listed here (the commit message describes a past-cap retryable-snapshot case).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
