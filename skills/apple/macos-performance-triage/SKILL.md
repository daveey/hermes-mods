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
counters. Do not distribute an accounting residual across selected native thread
families as if it were measured caller CPU: named threads can include already
accounted scopes, and excluded threads can contain unaccounted work. Keep PID,
revision and measurement window aligned. Before interpreting a slow-pass log's item count,
trace its argument in source: a returned delivery plan can be empty after expensive
candidate scanning, so zero delivered/claimed chats does not prove zero candidates or
justify skipping reconciliation. Separate accounting snapshot timestamps from live ps
sampling boundaries; do not combine their deltas into one measured interval. Native interpreter frames do not identify
the Python callback; a unique thread spawn proves its wrapper, not its initiating
operation. Label any cross-window proportional allocation as a sensitivity scenario,
not a basis for skipping real work. Do not blanket-exclude syscalls: SQLite `pread` chains identify useful
candidate work even though they do not establish time actually on CPU.

## Isolated before/after replay

When a live historical baseline is incomplete, keep controlled source replay separate from live Mac recovery. Pin pre/post source and dependencies, snapshot fixture SQLite stores with the backup API, hash identical inputs before each fresh process, and use a fixed offered schedule in balanced ABBA order with real monotonic and CPU clocks. Record service time and offer lateness separately. If retained evidence has expired, disclose any fixed evidence-wall-time replay: it does not prove live freshness or cache expiry. Preserve failed setup/pilot runs outside the qualifying series. Compare normalized output semantics explicitly, naming omitted clock fields; identical hashes do not prove those fields. Classify derived projection-cache writes separately from authoritative input mutations, and verify both arms produce the same derived state. A reader benchmark does not measure an HTTP endpoint that only serves previously published cache; exercise its real producer/publication boundary before claiming endpoint responsiveness.

## Controlled source replay across background publication

Trace the actual request boundary before benchmarking: a published-wire HTTP handler does not compute the background reader. Exercise the real background tick and mounted router separately, recording producer-thread CPU, publication inclusion, serialized-client offer lateness and request service. Process CPU includes the in-process client; earlier publication changes response payload work, so equal offered requests alone do not justify a handler-latency improvement claim. Label a hydration-only replay with canonical reconciliation disabled, fixed evidence time and ASGI transport as such; it is not live Mac, freshness-lifecycle or socket proof.

Freeze the complete generated input inventory, including installation identity, before fresh-process ABBA trials. Use consistent SQLite backups without copied WAL/SHM and immutable reads of the frozen seed. Preserve output identity rather than normalizing it away. Audit authoritative main files together with WALs; compare derived projection-cache logical changes separately. New rental/AWS/session stores may initialize and differ despite identical model output: retain those differences and costs, never assert all output bytes unchanged. Count instrumentation belongs in separate runs, and failed short observation windows remain excluded pilots rather than invented completeness.

## Canonical writer replay boundaries

Before enabling reconciliation, trace its independent board/home discovery and dynamic courier client imports; a reader's path adapter does not isolate its writer. Exercise the genuine process-home path in a disposable process rather than bypassing explicit-home write refusal. Capture each owner's deferred and unreached results: identical published models can conceal an entirely deferred writer pass. Audit consistent SQLite backups/logical dumps including WAL-resident data and newly initialized ActionStores, not only main-file hashes.

In a sanitized disposable core fixture, retain the documented `HERMES_TEST_ISOLATION=1` marker: it arms production-store refusal and short-circuits the fallback pytest-ancestor walk. Omitting it can make a genuine writer invoke psutil parent birth checks above an immediate-parent proc allowlist; swallowed PermissionErrors then contaminate an exact-denial invariant. Trace denied reads with in-memory frames and an independently captured PID/PPID/start-tick chain in an import/setup-only diagnostic before rerunning the workload. Do not infer historical ancestry from PID offsets, broaden all proc access, or filter unexpected denials out of the rental assertion. Keep any validated self/parent metadata-read receipts semantically separate from the five intentionally denied rental subprocess attempts. Disclose the test marker's other effects (annotation caching/background probes) in replay claims.

Treat rental input readers as potential producers: a read can start asynchronous collector refreshes and attempt cloud subprocesses. Install trusted-code filesystem/network/subprocess guards before imports, preserve actual refused executables, and label resulting availability/fallback behavior. An audit hook is not an OS sandbox. Join the exact owned collector thread before final state capture. Empty ActionStore reconciliation proves no ask lifecycle transition; a single clean tick is not watcher/outbox/decision/retry lifecycle proof. For an actual ask extension, validate the fixture's full ownership/compression-query schema before interpreting deferral, and read canonical requests separately from wire formats that omit them. A source-admission skip may leave old reader timing rows: use actual call deltas, not retained diagnostics. Genuine writer initialization can backfill random event/recovery identities and create local outbox rows; preserve A/A identity differences and dependent references rather than globally stripping them or claiming delivery. Narrow exact ask/store parity separately from whole-bundle equivalence.

## Finite ask lifecycle and identity parity

For canonical ask tests, read the actual ActionStore and canonical decision refresh: a wire route can deliberately omit requests. Widen a generated seed only for demonstrated real schema requirements, preserving the original seed and failed pilots. Exercise absent→open→unchanged→system withdrawal with the genuine core transition; distinguish fixture SQL block ingress from actual user ingress. Do not attribute retained reader timing rows to a phase the producer skipped.

If genuine writer initialization generates UUIDs, preserve final bundle differences even in A/A. Trace event IDs/UIDs to immutable backed-up DB rows, recovery predecessors to the blocked event, source references to the transition event, and recompute dependent contract/model hashes from exact source. A value mapping can establish relational consistency but never identity/custody equality. Equal ActionStore tables do not prove equivalence of identities absent from those tables. Pending local outbox creation is not delivery; audit WAL-aware final state and join owned collector threads. Once the relational audit and producer/API proofs are complete, consolidate coverage rather than expand into unrelated watchers/outbox/retry benchmarks; controlled source proof cannot manufacture a missing live operational baseline.

## Native confinement preflight

Resolve the real task scratch path before generating native sandbox rules, argv and HOME/temp roots: macOS scratch aliases can point to a `.noindex` directory, and lexical subpath rules then deny even the intended positive write. Preserve that failed pilot; fix only the demonstrated path mismatch, never widen production access. Independently review the corrected preflight and test scratch write/read allowed, synthetic sibling read/write denied, and loopback connect denied before a native workload. Label default-allow profiles precisely: file/network deny proof is not hermetic process/Mach isolation or proof that Electron works under the same confinement. A Python audit hook remains best effort.

For an owned renderer fixture, abort on a null render instead of acknowledging a wrongly wrapped model. Keep DOM/text serialization out of each timed acknowledgement; capture full output at final equivalence only. Denied requests and helper crashes invalidate the arm. Require canonical existing scratch, independent process-tree cleanup and a hard external controller deadline; fake-Electron/jsdom tests establish protocol logic, not native isolation, visible paint or user-window recovery. Exercise the deadline with an actual harmless child and deliberately undrained output, requiring the exact deadline outcome/exit code: a nonzero exit can instead be an epoll refusal on DEVNULL. Bound stderr as well as stdout. Retain spawned PID/argv immediately, before birth acquisition; on unavailable custody do not signal or delete, preserve unknown cleanup. Carry unresolved-child identifiers through timeout, truncation, read, close and reaper-start failures rather than flattening them to a generic snapshot error. Worker direct-child tests do not establish Darwin helper-tree supervision.

Treat Darwin `KERN_PROCARGS2` as an ambiguous argc-plus-NUL string blob, not an exact argv-boundary oracle: leading empty argv can be confused with padding and cause environment strings to be retained as arguments. Expected argv alone does not validate that boundary. Bracket each diagnostic executable-path read with exact birth/parent/uid observations; repeating two unbracketed snapshots can miss replacement within both snapshots. Birth and path equality do not prove exec-generation continuity. Keep custody enrollment/signaling disabled until a separately reviewed native mechanism establishes the required identity; Linux ctypes fixtures prove ABI/parser behavior only. Verify actual native wrappers against XNU buffer conventions, including the argc header beyond ARG_MAX, rather than testing only fabricated API returns.

For cooperative abort channels, explicitly opt in to an inherited read-only pipe, reject O_PATH descriptors where supported, and use the platform selector rather than select's low-fd bitmap. Fail closed on listener startup/read/select exceptions and test high descriptors, EOF, idempotent stop and leaked stop-pipe cleanup. Preserve cancellation status and original exception chains when thread/audit cleanup fails. Check cancellation around final snapshot/write/emit boundaries, but disclose the last-call race: the parent must invalidate any arm it cancelled even if a result raced out. A retained result file is not success. If invalidating it fails, retain that unlink error in the cancellation chain instead of suppressing OSError.

## Darwin process identity versus argv authority

Keep read-only identity observations separate from launch/signal authority. On Darwin, the source-defined private-header `PROC_PIDT_BSDINFOWITHUNIQID` flavor18 combines 136-byte BSD identity with 56-byte unique-ID/version metadata; validate the exact192-byte return against pinned OS source and test actual target compatibility. Bracket an executable-path read with the full combined identity, not birth alone. Equal endpoint observations still do not prove absence of intervening exec or mutable argv. The signed32-bit identity version may be negative; do not reject it by an invented positivity rule.

Do not infer exact argv boundaries from KERN_PROCARGS2 executable-path NUL padding: legal leading-empty arguments can make environment bytes look like arguments. A remembered byte-exact spawn vector is initial-launch provenance, not independently observed arbitrary current argv, and direct-child provenance says nothing about Chromium-created helper argv. Settle that contract with the creator before implementing a forced-cleanup path; never silently substitute diagnostic fields or broaden signal authority. A reviewed own-process stdin observer can validate native ABI/library/path compatibility without a workload, but cannot certify suspended spawning, exec-change rejection, helper custody or Electron confinement. Retain its exact source-closure hashes and scope, and avoid repeatedly rerunning a passed observer as performance evidence.


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
