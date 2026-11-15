# Artifact custody: bounded I/O, declared-only uploads and completion warnings

Mirror snapshot 4352; ordinals 4173, 4187, 4207.

Entries:

- **4173**: fix(kanban): bound artifact custody I/O, upload only declared artifacts, keep copies on the task's board
- **4187**: kanban: one large scratch artifact under two spellings no longer crashes completion
- **4207**: fix(kanban): surface custody warnings in kanban_complete; drop executor-local attachment_path

## Behavior

Completing or requesting review of a Kanban card can record attachments (declared artifacts) and evidence refs with a digest and retained copy.

- **No large-file I/O under the board write lock** (4173): files larger than `KANBAN_ATTACHMENT_MAX_BYTES` are hashed (and, for managed scratch, copied) before the write transaction. Under the lock only a stat identity re-check and a rename happen. A large file that changed or failed its pre-lock read is never read under the lock: scratch fails closed (`ArtifactPreservationError`, the task stays running); elsewhere the record carries its size and a "not hashed" warning.
- **Declared artifacts only are uploaded** (4173): records carry `role=artifact|evidence`. Evidence keeps custody (attachment, digest) without a native chat upload; evidence refs that name no file (API routes, `/path:123`, commit descriptions, existing directories) get no receipt record and no "File missing" line. Retained copies resolve the task's own board, not the current board. The gateway digests receipt files off the event loop.
- **One file, two spellings** (4187): a large scratch file declared as `<ws>/x.bin` and `<ws>/./x.bin` (or an artifact plus an evidence ref) used to crash completion with `AttributeError`; the claimed retained path is now reused and a managed entry with no claim and no staged copy fails closed.
- **Warnings and paths** (4207): the `kanban_complete` result carries `artifact_receipt` and `artifact_warnings` lines, so a worker sees a missing declared file. New receipt records no longer store the executor-local `attachment_path`; the worker-context handoff display machine-qualifies legacy paths.

## How it works

- `hermes_cli/kanban_artifact_prestage.py` (new) reads/copies before `write_txn` and caches entries by expanded path; `_fill` skips a path already cached; `take()` hands entries to `record_files` in `kanban_artifact_receipt.py`, which re-verifies (dev, inode, size, mtime_ns) and renames the copy, and removes unclaimed temp copies on exit.
- `gateway/kanban_watchers.py` uses `asyncio.to_thread` for delivery digests; `hermes_cli/kanban_artifact_delivery.py` selects declared artifacts for upload; `hermes_cli/kanban_db.py` and `kanban_db_context.py` / `kanban_metadata_files.py` carry board resolution and handoff display; `tools/kanban_tools.py` surfaces the warnings.

## Code

Shipped non-test paths (union over these entries):

- `gateway/kanban_watchers.py`
- `hermes_cli/kanban_artifact_delivery.py`
- `hermes_cli/kanban_artifact_prestage.py`
- `hermes_cli/kanban_artifact_receipt.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_context.py`
- `hermes_cli/kanban_metadata_files.py`
- `tools/kanban_tools.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_artifact_custody_bounds.py`
- `tests/hermes_cli/test_kanban_artifact_delivery.py`
- `tests/hermes_cli/test_kanban_receipt_artifacts.py`
- `tests/hermes_cli/test_kanban_scratch_artifact_custody.py`
- `tests/tools/test_kanban_complete_artifact_warnings.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- Old receipt records are not rewritten; legacy artifacts lists still contain executor-local paths for consumers that need them.
- The scratch-fail-closed path leaves the card running; retrying with the same metadata and a stable file is expected.

## Plugin?

None.
