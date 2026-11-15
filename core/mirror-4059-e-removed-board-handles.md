# Sweep idle handles for removed Kanban boards

Mirror ordinals: 4006.

## Behavior

Closes cached board connections and safe-read descriptors after a board disappears even when nobody reads that path again.

## How it works

A shared sweep runs after board enumeration each sync round and on gateway housekeeping ticks. It skips never-imported caches and closes idle sync BoardWatch, shared cached_ro and per-thread kept readers, then releases unlinked pread fds. BoardWatch moves to its own module and remains re-exported. Only FileNotFoundError/NotADirectoryError prove removal; permission errors keep handles. Nonblocking borrower locks and cache-entry identity rechecks avoid closing an active/replaced entry.

## Code

- `gateway/run.py`
- `gateway/run_housekeeping_disk.py`
- `hermes_cli/kanban_gone_board_handles.py`
- `hermes_cli/kanban_kept_reader.py`
- `hermes_cli/kanban_reader_retention.py`
- `hermes_cli/kanban_sync_board_watch.py`
- `hermes_cli/kanban_sync_client.py`

## Tests

- `tests/hermes_cli/test_kanban_gone_board_handles.py`: A real sync-loop round after directory removal leaves no /proc/self/fd descriptors. Separate cases cover live-board preservation, shared/per-thread readers including another thread, active borrowers, safe pread release, missing versus EACCES paths, unimported caches, isolated SQLite close errors, concurrent close/reopen rechecks and gateway housekeeping invocation.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Descriptor assertions require /proc/self/fd and skip elsewhere. Sweeping is process-local; it neither deletes board files nor repairs unrelated processes. Borrowed readers remain open until a later safe opportunity.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
