# TUI/e2e CI timing stability

Patches: 1283 1297 1298.

## Behavior

Test and infra changes so renderer tests stop timing the CI disk and layout:

- 1283 (#326): `canonicalStartupImage` and `receiptBusyRenderer` tests synchronize on receipt and startup outcomes instead of polling.
- 1298 (#337): all physical flushes (fsync of the pending-input journal, its directory, and staged image bytes) route through `ui-tui/src/lib/durableFlush.ts`, the only flush site. `pendingInputs` writes temp, flushes the fd, renames, then flushes the directory (same ordering as before); `imageAttachments` flushes through it. `vitest.setup.ts` replaces the flush with no-ops for every suite (writes, renames and reads still hit the real journal). An ESLint guard bans `fsync`/`fdatasync` imports, `flush:true` and `.sync()`/`.datasync()` (including member calls) outside the seam. `submissionDestinationRenderer` and `busyQueueAdmission` tear down in runner-owned `afterEach`. No assertion or timeout changed.
- 1297 (#334): `test_resize_scrollback` also tolerates the glyph layout's rule-framed stale preview row (`── ⠋ … words · 1s ──`), same cap of 3.

## Code

`ui-tui/src/lib/durableFlush.ts`, `pendingInputs.ts`, `imageAttachments.ts`, `ui-tui/vitest.setup.ts`, `vitest.config.ts`, `eslint.config.mjs`, `tests/e2e/core/cli_tmux/test_resize_scrollback.py`.

## Tests

`ui-tui/src/__tests__/durableFlush.test.ts` (ordering of temp-record/rename/directory flush, directory flush after unlink, image bytes before path, real module issues fd and directory fsyncs), `busyQueueAdmission.test.tsx`, `submissionDestinationRenderer.test.tsx`, `canonicalStartupImage.test.ts`, `receiptBusyRenderer.test.tsx`. The 1297 e2e tolerance has no new test of its own. This mirror task did not execute them.

## Applying

Apply in order 1283, 1297, 1298.

## Gotchas

The root cause was no seam between writing the journal and making it physically durable. New direct flush sites now fail lint. No plugin involved.
