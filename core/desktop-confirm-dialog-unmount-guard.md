# Desktop ConfirmDialog arms no close timer after unmount while onConfirm is pending

Patches: 1234

## Behavior

`ConfirmDialog` no longer schedules its 600 ms close timer when the dialog was unmounted while `onConfirm` was still pending. Before, `run()` resumed after `await onConfirm()` on a dead tree and armed a fresh timer that the unmount cleanup had already missed. The timer then fired after the test environment was torn down (`window is not defined`) and failed an unrelated workspace test on CI.

## How it works

`run()` re-checks an `unmountedRef` after the `await` and returns without arming the timer when the component has unmounted; the unmount effect sets the ref and the mount effect clears it. The earlier fix only covered an unmount during the 600 ms window; this one covers an unmount during the pending confirm.

## Code

- `apps/desktop/src/components/ui/confirm-dialog.tsx`: the `unmountedRef` guard in `run()` and its mount/unmount effect (12 added lines).

## Tests

- `apps/desktop/src/components/ui/confirm-dialog-unmount.test.tsx`: `no close timer is armed when the dialog unmounts while onConfirm is pending` renders the dialog with a never-resolved `onConfirm`, clicks the confirm button, unmounts, resolves the confirm, advances fake timers by 1000 ms and asserts `onClose` was not called. It was not re-run for this mirror.
- Mounted-dialog behavior (the close timer still fires when the dialog stays mounted) is covered by the pre-existing tests in the same file, not by this patch.

## Applying

Apply the whole numbered series at the merge-base with `git am -3 --empty=keep`; this patch edits the test file created by the earlier unmount-timer fix, which is part of the same series.

## Gotchas

- The guard must sit after the `await`; a check before it misses the leak.
- The failing symptom appears in an unrelated test file, so a green local run proves little: the timer rarely outlives teardown.
