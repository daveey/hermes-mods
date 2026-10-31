# Serialized disk-cleanup tracked writers

Patch 1370; source `12d643cf97a685750a366c4a3e5c9319a39c6c7b`.

## Behavior

Concurrent tracked-state changes are serialized across threads and POSIX processes. A tracking hook failure is logged instead of escaping post_tool_call.

## How it works

A reentrant thread lock plus outermost flock protects read/change/write; atomic_json_write uses unique temporary files and retains a backup. The empty-directory sweep stays outside the lock.

## Code

plugins/disk-cleanup/disk_cleanup.py; plugins/disk-cleanup/__init__.py

## Tests

Source regression inventory: tests/plugins/test_disk_cleanup_plugin.py::TestConcurrentTrackedWriters: thread and subprocess retention, no shared temporary name, backup and hook failure logging.

These runtime suites were not rerun in this mirror pass. Executed mirror inventory/replay/tree checks are recorded in [mirror-1375-verification.md](mirror-1375-verification.md); they do not certify runtime behavior.

## Applying

Apply the complete snapshot using the [README replay recipe](README.md#applying), not this patch independently.

## Gotchas

Non-POSIX systems use the thread lock only; cross-process serialization there is untested.

## Plugin?

This patch mirrors the source implementation; no separate public mod installation is required.
