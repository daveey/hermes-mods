# Desktop instant archive with soft-hide

Patches: **2333–2334**.

## Behavior

Archive no longer waits for the linked-work preflight or offline check before the row leaves the sidebar. The row is soft-hidden and the open chat deselected as soon as archive is invoked; a cancelled linked-work dialog, unresolved owner, failed offline queue or failed archive RPC restores it. The confirmation dialog itself is unchanged.

## How it works

`archive-soft-hide.ts` hides the row and keeps a restore record. The follow-up (2334) fixes three review regressions: offline-cached rows are not pre-hidden by bare id (their owner-scoped queue path hides them, so another owner's cached chat with the same id is not tombstoned); cancel re-inserts only the pins the archive removed, keeping pin edits made while the dialog was open; rollback puts back only a row a live slice actually held, never a cache-only row.

## Code

`apps/desktop/src/app/session/hooks/use-session-actions/archive-soft-hide.ts` and `index.ts`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `archive-linked-confirm.test.tsx` in the same directory: row hidden before slow preflight/RPC settle, hidden during dialog and back on Cancel, back on RPC failure, offline row handling, pin-edit preservation, no tombstone of another owner's cached chat, failed offline queue stays cached and out of the live list.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

The patch descriptions state the new regression tests fail on the previous head; this mirror did not re-prove that. Offline rows deliberately keep the prior (non-instant) path.

## Plugin

Desktop app code in core; no plugin required.
