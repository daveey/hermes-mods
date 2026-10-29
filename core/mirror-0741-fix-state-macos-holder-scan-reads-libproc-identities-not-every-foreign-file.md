# fix(state): macOS holder scan reads libproc identities, not every foreign file [daveey]

Patch 0741, source `a408ba1b05d2d1add193dcb7b0f878522bcc94b7`.

## Behavior

fix(state): macOS holder scan reads libproc identities, not every foreign file [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def foreign_state_db_holders(db_path: Path) -> list[tuple[int, str]]:`
- `while never live-downgrading an on-disk WAL database and never flagging an ordin`
- `class TestDetectCrossVmFs:`
- `the guest entry point applies it directly.`
- `def test_guest_barriers_leave_synchronous_alone_when_unset(monkeypatch, tmp_path`
- `def test_windows_restart_manager_scan_sizes_then_excludes_self(monkeypatch, tmp_`
- `import pytest`
- `class TestTranscriptWritePatience:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> On macOS, 65 tests in 19 tests/hermes_state files failed on every run, while
> Linux CI stayed green. In most of them, repair or VACUUM refused with "a live
> writer still holds state.db".
>
> Five whys:
> - Why did repair refuse? foreign_state_db_holders returned (-1, "open-file
>   scan failed: TEST BUG: file I/O against the REAL hermes home:
>   ~/.hermes/logs/hermes-tunnel.log"), and live_writer_holds_db fails closed
>   on that sentinel.
> - Why did the scan touch that file? On darwin it used
>   psutil.Process.open_files(). That calls isfile_strict(), which does an
>   os.stat on every file every process has open. The leg then realpath'd
>   each path. Any other Hermes process's log file is in that set.
> - Why did that fail? The test home-I/O guard refuses stat/lstat under the
>   real home. It exempts only /proc metadata, which is how the Linux leg
>   inspects descriptors (by readlink plus inode identity, with no foreign
>   file I/O).
> - Why did the two legs differ? The darwin leg matched textual paths
>   (realpath on both sides, 9b419a2d3c) instead of identities, so it needed
>   file I/O on every foreign path.
> - Not caught: CI runs tests/hermes_state only on Linux. The macOS lane
>   runs only `-m platforms` tests, and nothing exercised the darwin leg on
>   Linux.
>
> Production on a Mac was not blocked. With the real runtime and no guard, a
> quiet copy of state.db scanned clean, and repair took its backup and ran its
> strategies. The verdict still depended on stat-ing every foreign file.
>
> Fix: on darwin, enumerate descriptors through libproc
> (hermes_state_dbfile._iter_darwin_fd_targets, already used by the
> deleted-WAL guard). It gives the kernel path and the vnode (st_dev, st_ino)
> with no file I/O. A process counts as a holder when its identity is the
> db/-wal/-shm identity, or when its case-folded path names a watched file:
> that covers a retired generation, which psutil silently dropped. Live on
> the MacBook, this found the same 9 holder pids as psutil, in 0.04 s
> instead of 0.14 s.
>
> Detection: test_state_db_holders.py fakes the libproc layer and forces the
> darwin leg, so it runs on Linux CI. It refuses any stat, lstat or realpath of
> a foreign path, and checks identity aliasing, self-exclusion, retired
> generations, case folding and fail-closed.
>
> The remaining 5 macOS-only failures were test assumptions:
> - test_cross_vm_fs_wal_refusal: the mountinfo parser test now presents as
>   Linux. _detect_cross_vm_fs returns False off Linux before it parses.
> - test_guest_durability_barriers: macOS always enforces synchronous=FULL.
> - test_write_lock_patience: the holder now holds until released. A 1 s
>   busy_timeout lasts about 3 s with macOS SQLite, so the 2 s hold ended
>   inside the first wait.

## Code

- `hermes_state_holders.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_state/test_cross_vm_fs_wal_refusal.py`
- `tests/hermes_state/test_guest_durability_barriers.py`
- `tests/hermes_state/test_state_db_holders.py`
- `tests/hermes_state/test_write_lock_patience.py`

New or changed test definitions present in the source diff:

- `test_only_virtiofs_and_9p_mounts_are_flagged`
- `test_darwin_scan_never_touches_foreign_files_and_finds_holder_by_identity`
- `test_darwin_scan_quiet_store_is_clean_despite_foreign_files`
- `test_darwin_scan_counts_a_retired_generation_and_case_folded_path`
- `test_darwin_scan_failure_fails_closed`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
