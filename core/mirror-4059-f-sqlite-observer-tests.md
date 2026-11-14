# SQLite observer restore and install-epoch boundaries

Mirror batch 6; ordinals 4017, 4053.

## Behavior

Test-only contracts distinguish same-inode restoration and cursor reinstall from unrelated schema changes.

## How it works

The restore fixture explicitly advances mtime after an equal-size copy, rather than relying on filesystem timestamp ticks. The cursor fixture deletes only the revision singleton, then compares all non-epoch identity fields across installation repair.

## Code

Source paths touched by this feature group (including regression fixtures):

- `tests/hermes_state/test_session_change_cursor.py`
- `tests/tui_gateway/test_watch_store_gate.py`

Mail entries in the ordered series:

- 4017: [`62be3718f8aa600c8687a5b92e92aedd877d5710`](patches/4017-daveey-Deflake-same-inode-SQLite-restore-timestamp-fixture.patch)
- 4053: [`87caa5246315e3f6dfa5a8fd5a32ff8857844587`](patches/4053-daveey-Isolate-reinstall-epoch-invalidation-from-schema-cookies.patch)

## Tests

- `tests/tui_gateway/test_watch_store_gate.py`: inode and size remain equal, mtime changes, the observer token changes, and a reopened connection reads restored value 99 instead of 1.
- `tests/hermes_state/test_session_change_cursor.py`: missing singleton yields FULL; reinstall changes epoch while device/inode/SHM/schema/lineage/revision stay equal; stable init performs no write and preserves the new cursor; later session creation is incremental.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

No observer or cursor implementation changes in these mails. The restore test establishes a changed-mtime contract, not detection of an indistinguishable byte replacement.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.
