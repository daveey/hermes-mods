# Loop accounting atomic snapshots and CPU attribution tests

Mirrored entries: 3876.

## Behavior

Tests pin atomic snapshot replacement, retention of old snapshots while their PID lives, periodic writer pruning, stop-time flush and nested-record CPU accounting. Linux procfs-specific tests require live unaccounted CPU and exited-thread CPU to remain visible.

## How it works

The snapshot test intercepts same-directory os.replace, then fails a replacement to prove the prior target survives and the temporary file is cleaned. A child process provides real live/dead PID evidence for prune. Writer events establish pruning without a fixed wait, and a long writer interval proves stop(write=True) performs the flush itself.

The record test separates nested counters from outermost CPU attribution. CPU-burning threads provide observable /proc/self/task work for unaccounted-live and exited-thread fields. A sync timing case requires board_end without a result to count an error.

## Code

These entries modify only the test files listed below; production behavior is unchanged.

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/hermes_cli/test_loop_accounting.py`: Checks replacement source/target paths and failure atomicity, real child PID prune behavior, writer pruning and stop flush, counters/outermost CPU totals, missing sync result errors, and nonzero unaccounted/exited thread CPU under procfs.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

Production accounting code is unchanged here. Procfs cases skip when unavailable, so this is not cross-platform CPU proof. Timing/load-sensitive assertions describe test obligations, not measurements produced by this pass.

## Plugin?

No new plugin. These are core accounting regression tests.
