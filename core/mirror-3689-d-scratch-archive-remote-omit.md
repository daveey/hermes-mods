# Scratch archive: omit objects the remote already holds

Patches: **3661, 3675–3676**.

## Behavior
- For a standalone repo with remote-tracking refs of a network remote, the complete scratch archive leaves out `.git/objects` files the remote already has, and clean tracked files matching blobs of the nearest pushed commits. A supplement pack carries local-only objects (unpushed commits, staged blobs, stash, reflog-only, dangling). The commit message reports archive size for a full clone falling from roughly 57-80 % of the source to under 1 %.
- Repos with only local-path remotes, shallow or sha256 repos, or any planning error keep every object as before (v1 layout).
- A recorded remote-tracking tip counts as held only while the remote still advertises it or an advertised branch contains it; otherwise its objects go in the supplement pack. Any probe failure keeps all objects.
- The archive manifest becomes version 2 with omission records; verification checks them and pack hashes, and an older core refuses v2.
- A `restore` helper rebuilds the tree from the archive by cloning the remote and fetching recorded tips; it fails loudly if the remote lacks objects.
- Scratch GC tests that call the fd-path helper are marked `platforms("posix")` so the macOS CI lane covers them.

## How it works
3675 adds `kanban_scratch_archive_omit.py` (plan, supplement pack verified by index-pack, omitted-file manifest, restore) and wires it into `kanban_scratch_archive.py`; late objects or rewritten omitted files refuse the archive and keep the workspace. 3676 adds `_verified_tips` (ls-remote plus a commits-only bare clone and branch walk, in a neutral temp dir with the user's git config, non-interactive), stops treating promisor packs as proof by themselves, skips fetching tips the clone already holds on restore, and restores modes/mtimes in read-only directories. 3661 marks the fsync-spy tests posix-platform and asserts the injected fault actually fired.

## Code
- `hermes_cli/kanban_scratch_archive.py`
- `hermes_cli/kanban_scratch_archive_omit.py`
- `tests/hermes_cli/test_kanban_scratch_gc_archive.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_scratch_gc_archive.py`: `test_remote_held_objects_are_omitted_and_restore_round_trips`, `test_omitted_archive_is_under_a_tenth_of_a_full_clone`, `test_local_path_remote_keeps_every_object`, `test_object_written_after_the_plan_keeps_workspace`, `test_clean_file_rewritten_after_hashing_keeps_workspace`, `test_verify_rejects_tampered_omission_records`, `test_restore_fails_loudly_when_the_remote_lacks_the_objects`, `test_only_network_remotes_count_as_holding_objects`, `test_blobless_clone_omits_promised_objects_and_restores_lazily`, `test_boundary_blobs_cover_unpushed_head`, `test_tip_the_remote_deleted_and_gcd_is_archived_not_omitted`, `test_tip_behind_a_moved_remote_branch_still_counts_as_held`, `test_unreachable_remote_keeps_every_object`, `test_restore_writes_omitted_files_into_read_only_directories`, `test_remote_probe_ignores_the_scratch_repos_own_config`.
- 3661 edits existing tests (platform markers, a hits assertion) and adds no new test names.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Restoring needs the remote reachable and still holding the objects; a restore of a large clone took minutes per the commit message.
- The probe adds network calls at archive time; failure falls back to the full archive.
