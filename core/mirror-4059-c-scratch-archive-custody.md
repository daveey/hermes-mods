# Scratch archiving, Board custody discovery and explicit preservation pins

Mirrored entries: 3859, 3891, 3892.

## Behavior

Scratch cleanup can archive bare repositories, linked worktrees/submodules, promisor-backed refs, FIFOs and sockets instead of refusing those shapes forever. It still keeps a tree when evidence is insufficient or archival fails.

Evicted scratch can obtain custody evidence for its default path; archived Board rows without completed_at can use a verified retention clock. Old unowned task-id trees can acquire Board-claim custody receipts without discovery itself deleting anything. `kanban.scratch_gc_preserve` pins trees across normal cleanup, custody and detached-trash removal, with malformed/unreadable settings preserving conservatively.

## How it works

Archive discovery classifies standalone, bare and gitlinked repos. Ref selection excludes remote-held tips and walks offline local/promisor object evidence: locally complete unpushed refs are bundled, promised refs remain reconstructible through payload metadata, and unvouched missing objects refuse. An outside gitdir is read through a private copied index, never modified; the worktree diff/untracked data and HEAD/branch are recorded. FIFOs/sockets have manifest type/mode entries with no content member; device nodes remain refused.

Custody retention for archived rows with null completed_at takes the newest valid Board updated_at (milliseconds converted to seconds) and ended-run time. Discovery limits candidates to exact lowercase task-id directories, no overlapping local/sibling owner path, no receipt and no preserve pin; it enumerates replica claim events but reads each authoritative task/event/run back from the Board and requires this host’s ended claims. It writes a local receipt and leaves the existing terminal/lease/session/job/archive/rename fences responsible for removal.

Preservation is checked by task id or path component, including detached gc sibling names. The follow-up checks detached trash before deletion and treats a nonmapping kanban section as unknown/preserve-all. Default-path eviction receipts require an existing plain managed tree.

## Code

- `hermes_cli/kanban_scratch_archive.py`
- `hermes_cli/kanban_scratch_archive_shapes.py`
- `hermes_cli/kanban_scratch_custody.py`
- `hermes_cli/kanban_scratch_custody_discovery.py`
- `hermes_cli/kanban_scratch_preserve.py`
- `hermes_cli/kanban_workspace_jobs.py`
- `hermes_cli/kanban_worktree_gc.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/hermes_cli/test_kanban_scratch_archive_shapes.py`: Checks promisor-tag exclusion, local bundle heads, promised payload restoration and unvouched-object refusal; bare-root/nested archives; inside/outside gitdirs and unchanged outside Git metadata; dangling/unsafe link refusal; fifo/socket manifests with no content and device-node refusal. Missing real content members still invalidate an archive.
- `tests/hermes_cli/test_kanban_scratch_gc_archive.py`: Updates linked-worktree/bare/fifo cases to require successful archives and removal, with a dangling-gitlink control retaining its tree.
- `tests/hermes_cli/test_kanban_scratch_custody.py`: Updates custody controls from now-archivable bare repos to an unarchivable shape, and removes null-path from the never-receipted cases. Existing custody checks remain controls.
- `tests/hermes_cli/test_kanban_scratch_custody_backoff.py`: Changes refusal fixtures to shapes that still refuse; retains custody archive-backoff coverage rather than relying on bare repos as permanent failures.
- `tests/hermes_cli/test_kanban_scratch_gc_backoff.py`: Uses dangling gitlinks to preserve retry/backoff, count-reset and malformed-memo coverage now that bare repositories archive.
- `tests/hermes_cli/test_kanban_scratch_custody_gaps.py`: Pins archived retention boundaries and live/uid evidence, Board-claim discovery without deletion, missing/foreign/unfinished evidence controls, local-row race recheck, transaction refusal, default-path receipts, preserve pins across cleanup and nested paths, no walk for pinned rows, detached-trash pinning and malformed/unreadable-config preservation.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

A linked worktree with an external gitdir does not bundle someone else’s repository; its recorded base still matters for restoration. FIFOs/sockets preserve identity metadata, not runtime endpoints. Discovery is bounded and requires authoritative readback; absent Board/replica proof leaves owner_unverified trees intact. A preserve pin may deliberately retain substantial disk use; do not treat it as an archive success. No real workspace cleanup was run in this pass.

## Plugin?

No. Cleanup custody, archive completeness and deletion vetoes must cover every core removal path.
