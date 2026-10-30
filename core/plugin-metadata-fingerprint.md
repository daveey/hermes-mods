# Stable discovery records across metadata-only sync

Patches: 1137 1138.

## Behavior

- A metadata-only change in plugin ctime does not invalidate the served hook-presence or toolset-key records.
- A replacement file with the same size and restored file/directory mtime still invalidates the record when its inode changes.

## How it works

The discovery fingerprint omits ctime while retaining inode, size and mtime. The rename regression isolates inode changes, including restored directory mtime. A valid cached presence result avoids cold discovery; the existing invocation path still discovers when needed.

## Code

- `hermes_cli/plugins_snapshot.py`

## Tests

Source regression coverage:

- `tests/hermes_cli/test_plugin_hook_presence_record.py`
- `tests/hermes_cli/test_kanban_capabilities_plugin_snapshot.py`

See [mirror-1157-verification.md](mirror-1157-verification.md) for the exact files freshly executed for this publication and their results. Files listed here are source coverage references, not a claim that every file was rerun.

## Applying

Apply the complete numbered series at the frozen merge-base with `git am -3 --empty=keep`. Earlier patches include a curated integration overlay; individual entries are not promised to install independently. See [the inventory](README.md) and [verification report](mirror-1157-verification.md).

## Gotchas

An in-place edit that preserves inode, size and mtime is not detected by this stat-based fingerprint. This is not a content hash.

## Plugin?

No. This changes core lifecycle, admission, discovery, routing or its tests/workflows rather than a plugin-owned extension point.
