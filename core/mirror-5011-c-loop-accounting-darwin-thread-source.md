# Loop-accounting Darwin thread source in no-source fold tests

Patches: **4632** (5011 snapshot).

## Behavior

The no-source fold test no longer equates "hide `/proc`" with "no per-thread CPU". On macOS, `thread_cpu()` still uses `proc_pidinfo`, so hiding `/proc` alone left measured `cpu_s` on every row. The fold test stubs both sources. A platforms-marked macOS test covers live Darwin rows (CPU present, exited threads still fold).

## How it works

`test_without_procfs_snapshot_folds_exited_threads` stubs `_darwin_thread_cpu` as well as `/proc`. `test_darwin_thread_source_rows_carry_cpu_and_exited_threads_fold` is `@pytest.mark.platforms("macos")`.

## Code

- `tests/hermes_cli/test_loop_accounting.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_loop_accounting.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Linux CI has `/proc`, so the Darwin branch is dead there unless the macOS lane runs it. Product `loop_accounting.py` is unchanged in this ordinal.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
