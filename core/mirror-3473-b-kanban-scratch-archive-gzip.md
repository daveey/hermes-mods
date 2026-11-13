# Kanban scratch archive: gzip level 1 and uv `.git` marker as data

Patches: **3457**.

## Behavior
- Scratch workspace archives are written at gzip level 1 (was default level 9), cutting archive time sharply for large trees at the cost of a somewhat larger payload. Existing level-9 archives still verify.
- A regular `.git` file that is not a `gitdir:` pointer (such as the empty marker uv writes in its cache directories) is archived as ordinary data instead of refusing the whole workspace. A `gitdir:` file and a `.git` symlink are still refused.
- The marker is read through a no-follow descriptor checked against the scanned lstat, and its signature must match the payload snapshot, so a marker rewritten into a gitlink after judgement is refused.

## How it works
3457 sets `_GZIP_LEVEL = 1` in `_write_payload` and changes `find_repos` in `hermes_cli/kanban_scratch_archive.py` to classify non-directory `.git` entries by content, with the identity and signature re-checks.

## Code
- `hermes_cli/kanban_scratch_archive.py`
- `tests/hermes_cli/test_kanban_scratch_gc_archive.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_scratch_gc_archive.py`: `test_uv_git_marker_file_is_archived_as_data_and_round_trips`, `test_gitdir_pointer_file_is_still_refused`, `test_git_symlink_is_still_refused_without_reading_its_target`, `test_marker_judged_against_its_scanned_stat`, `test_marker_rewritten_into_gitlink_after_judgement_keeps_workspace`, `test_payload_uses_fast_gzip_level_and_old_level_9_archives_still_verify`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Level 1 payloads are larger (reported ~205 MB vs ~180 MB on one large tree); the trade is faster disk reclaim.
