# [daveey] checkpoints: price git add before running it; persist the slow-folder back-off in the store

Patch 0712, source `72755549d128f853cc541e3ce05c11ba601d879a`.

## Behavior

[daveey] checkpoints: price git add before running it; persist the slow-folder back-off in the store. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `class TestGitTimeoutRecovery:`
- `class CheckpointManager:`
- `The size cap (checkpoints.max_file_size_mb) has to act BEFORE staging. git`
- `from typing import Optional`
- `_MTIME_SLACK_S = 2.0`
- `def scan_tree(path: str, cap_bytes: int, max_entries: int) -> TreeScan:`
- `def clear_timeout_debris(store: Path, since: float, index_file: Optional[Path])`
- `With a container terminal backend (docker, singularity, modal, daytona,`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> agent-23a0c3b69f came back after 4d28e047a7: on the MacBook at 2026-10-28T08:48:30Z
> `git add -A` timed out again in /Users/me/code/_wt/resume-stale-managed-pin (a
> hermes-core-wt checkout). The size cap only keeps files over max_file_size_mb out of
> the add; every other file still costs a hash, and on a first snapshot a compressed
> object write. Measured on hermes-hub (16 cores, load 11-14): a hermes-agent checkout
> (19,021 files, 209 MB, nothing near the cap) takes 37 s on its first add, so on a
> laptop at load ~56 it cannot finish in 60 s. The back-off that followed lived in one
> process's memory, so every new process paid the 60 s again.
>
> - tools/checkpoint_staging.py: the tree walk also keeps size and mtime of every file
>   under the cap. estimate() prices the add from those (1.1 ms per file + 28 MB/s,
>   the hub's first-add rates, scaled by load average / cores); budget = half the add
>   timeout. The slow-folder back-off is written to <store>/info/slow-dirs.json (wall
>   clock, clamped to one period) as well as the process registry.
> - CheckpointManager._take: before git add, a cold index prices every file and a warm
>   one only files modified since the last snapshot. Over budget, git ls-files -o -m
>   (new + stat-changed, gitignored dropped; no hashing) narrows it. Still over: skip
>   the add, mark the folder slow, and log the folder, the estimate and the 3 biggest
>   files. Returns in ~2 s instead of blocking the turn for 60 s.
> - The index is seeded with `read-tree -m`, which keeps the stat data of unchanged
>   entries, and its mtime is restored to the last add's, so git rehashes only changed
>   files (a plain read-tree made it rehash the whole tree every turn: 18.8 s vs 0.03 s
>   for the hermes checkout) and the estimate's "changed since" anchor survives a
>   skipped or killed add.
> - ensure_checkpoint reads the back-off from the store, so a fresh process skips too.
>
> Coverage limit: a tree that is over budget on a cold index (first snapshot of a big
> checkout on a loaded host) stays unsnapshotted, retried hourly, until the host is
> idle enough to fit it in budget. Before this change it timed out instead.
>
> Tests (tests/tools/test_checkpoint_manager.py::TestStagingBudget):
> - test_sub_cap_tree_over_budget_is_skipped_without_git_add (400 x 64 KB at load x40:
>   no git add, < 5 s, warning names folder/budget/biggest file) - red on main
> - test_back_off_survives_a_new_process[budget|timeout] (fresh interpreter, real load
>   factor, same store: no git add) - red on main
> - controls, green on both: the same tree is snapshotted when idle; a warm index
>   prices only changed files; gitignored weight does not count.
> Live (real manager, no patches, 300 x 9 MB tree on the hub): main blocked 61.6 s
> (timeout) and a second process 60.3 s; this commit skips in 2.2 s and a second
> process in 0.0 s.
>
> Undo: git revert this commit.
> Hub card t_01a181f638cd95f0.

## Code

- `tools/checkpoint_manager.py`
- `tools/checkpoint_staging.py`
- `website/docs/user-guide/checkpoints-and-rollback.md`

## Tests

Source test files changed by this commit:

- `tests/tools/test_checkpoint_manager.py`

New or changed test definitions present in the source diff:

- `test_sub_cap_tree_over_budget_is_skipped_without_git_add`
- `test_same_tree_is_snapshotted_when_the_host_is_idle`
- `test_warm_index_prices_only_changed_files`
- `test_gitignored_weight_does_not_count`
- `test_back_off_survives_a_new_process`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
