# Scratch archive, retry and empty-tree safety guards

Patches: 3787, 3788, 3795, 3805, 3839.

## Behavior

Pins archive omission safety and scratch-GC retry records, and tightens the commit-less repository proof used before moving work to the pool. These are fail-safe checks, not permission to discard uncertain repository state.

## How it works

The backoff tests change a refused tree's directory mtime or completion time and require a fresh count/base delay. Additional tests straddle the supported epoch bound and require an ISO UTC retry log.

Archive tests exercise real fixture clones: shallow repositories retain all objects; a clean tracked file with another hard link stays in the tar so inode relationships survive restore; reconstructed omitted blobs must match both recorded size and SHA-256 even if archive-manifest validation itself accepts the record.

`kanban_move_pool._commit_less` already probes HEAD and refs. Its refs proof now requires return code zero, no stdout and no stderr: Git can skip a broken ref with exit zero and only a warning. Tests also cover an empty ref, an erroneous HEAD probe and a refs listing failing silently. A genuinely empty initialized repository remains the intended positive case.

## Code

- `hermes_cli/kanban_move_pool.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/hermes_cli/test_kanban_scratch_gc_backoff.py` (patches 3787, 3795).
- `tests/hermes_cli/test_kanban_scratch_archive_omit_guards.py` (patch 3788).
- `tests/hermes_cli/test_kanban_tree_state_unborn.py` (patches 3805, 3839).

- **3787:** Changing directory mtime or completion timestamp after two refusals resets count to one, retry delay to BASE_S and the stored fingerprint to the changed tree.
- **3788:** A shallow clone retains all objects; a hard-linked tracked file stays whole and restores shared inode/bytes; tampered omitted size or SHA-256 is refused before the target file appears.
- **3795:** Boundary-parameterized memo records enforce the safe epoch limit and retry logging must include the expected ISO UTC timestamp rather than a raw epoch.
- **3805:** Garbage and zero-length refs plus a failing HEAD probe all preserve the unpushed-commits fail-safe rather than proving an unborn repository.
- **3839:** A silent failing for-each-ref subprocess still returns the unpushed-commits fail-safe, even when both output streams are empty.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

Archive verification and restored-content verification are separate gates. An empty refs stdout is insufficient when Git emitted a warning or failed. Epoch tests protect portability before platform time conversion; a tree fingerprint change must not inherit the old refusal streak.

## Plugin?

No. The archive format, restore validation, GC memo and placement tree-state gate are internal core contracts.
