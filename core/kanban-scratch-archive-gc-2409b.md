# Archive complete scratch trees before guarded garbage collection

Patches: **2366–2368**.

## Behavior

Garbage collection of a scratch workspace first writes a complete `tree.tar.gz` plus `manifest.json` archive (including non-git and symlink trees, ignored files and siblings), verifies it, and removes the workspace only if it is unchanged. Any source mutation, special file, reserved-name collision, tampered payload, write or fsync failure keeps the workspace and recovery evidence.

## How it works

`kanban_scratch_archive.py` scans by directory descriptor without following links, records signatures, rejects a regular file swapped for a FIFO, and fsyncs files then directories bottom-up, including every ancestor entry (2368), before publication via a no-replace rename. Detach/finalize run in short Board fences with a recovery intent kept on partial failure. 2367 reads manifests BOM-safe.

## Code

`hermes_cli/kanban_scratch_archive.py`, `kanban_scratch_custody.py`, `kanban_worktree_gc.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_scratch_gc_archive.py` and `test_kanban_scratch_custody.py` (mutation during/after archive, equal-size rewrite, symlink ancestor swap, FIFO swap, crash after detach, fsync ordering and failures, idempotent repeat GC).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Archive scans run outside transactions and are re-checked afterward, so a busy workspace is kept rather than archived. A pickup after archive gets a new tree.

## Plugin

Core change; no plugin required.
