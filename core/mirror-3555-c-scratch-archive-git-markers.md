# Scratch archive: .git marker judging through the scan directory fd

Patches: **3550, 3551**.

## Behavior
- The scratch archive judges each regular `.git` marker through the directory fd the scan already holds, instead of re-walking the path from the root per marker.
- A `.git` marker that was never judged counts as a gitlink and the workspace is kept (fail closed) instead of raising `KeyError`.
- Errors while judging a marker surface as the scan's existing "scan uncertain" refusal.
- The gzip level 1 size trade-off (about 14% larger payloads than level 9, much faster) is documented next to `_GZIP_LEVEL`.

## How it works
3550 adds an `on_git_file` callback to `_scan`, called for each regular `.git` while its directory fd is open; `_find_repos` judges markers there. The O_NOFOLLOW open, the scanned-lstat signature match and the post-write marker re-check are unchanged. The `_GZIP_LEVEL = 1` note records measured timings and says to set 6 to reverse it (readers need no change). 3551 replaces `verdicts[gitrel]` with `verdicts.get(gitrel, True)` so a filter drift between judge and loop cannot escape the `ArchiveRefused` contract.

## Code
- `hermes_cli/kanban_scratch_archive.py`
- `tests/hermes_cli/test_kanban_scratch_gc_archive.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_scratch_gc_archive.py`: `test_marker_read_through_the_scans_own_directory_fd`, `test_unjudged_marker_fails_closed`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The gzip level change (size versus time) is untested.
