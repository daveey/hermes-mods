---
name: macos-performance-triage
description: "Use when a Mac eats CPU. Separate stacks from CPU counters."
---

# Native macOS stack triage

Measure process CPU-time deltas over a bounded interval to establish CPU usage.
Use native stacks to identify candidate work, not to replace that measurement.

For a process you are authorized to inspect:

```sh
sample <pid> 30 5 -file sample.txt
python3 scripts/sample_oncpu.py sample.txt
```

Run the script relative to this skill directory, or use its absolute path. It
reports **filtered stack occupancy** by thread family and native chain, excluding
known wait leaves and omitting interpreter dispatch frames from displayed chains.
Percentages are shares of retained stack samples, not process CPU percentages.
Retained leaves can still block (including file I/O); native wall-stack occupancy
is not measured CPU time or cores. Confirm attribution with process/thread CPU
counters. Do not blanket-exclude syscalls: SQLite `pread` chains identify useful
candidate work even though they do not establish time actually on CPU.

## Wait classification and regression tests

Match exact leaf names, not caller substrings or all symbols ending in `_trap`.
The Mach semaphore wait family consists of `semaphore_wait_trap`,
`semaphore_wait_signal_trap`, `semaphore_timedwait_trap`, and
`semaphore_timedwait_signal_trap`. Each includes a wait; signal-only semaphore
traps are not wait leaves. Names are defined in Apple's
[XNU syscall table](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/mach/syscall_sw.h),
with wait implementations in
[sync_sema.c](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/sync_sema.c).

Keep regression input synthetic and exercise the script's CLI: caulk semaphore
waits must disappear while SQLite `pread` chains remain. Test related wait traps,
signal-only traps, and unknown syscalls independently. Never publish raw samples
from a real user process; they can contain private symbols and paths.
