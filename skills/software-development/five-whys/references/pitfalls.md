# Pitfalls

Reference for skill `five-whys` (moved verbatim from SKILL.md).

When investigating runtime integrity failures, read `references/runtime-integrity-pitfalls.md` (receipt evidence, protocol logs, OOMs, SQLite, sandbox teardown and merge enforcement).

When diagnosing scheduling or observation flakes, read `references/concurrency-test-pitfalls.md` (process probes, sockets, cache stamps, queue reads and CLI/PTY synchronization).

When investigating authorization or plugin isolation, read `references/scope-and-dependency-pitfalls.md` (spend authority, credential scope, module lifetime and dependency identity).

When fixtures depend on event chronology or expiry, read `references/clock-fixture-pitfalls.md` (PR assignment ordering and receipt TTL clocks). For exact-boundary virtual-clock tests, inject fractional origins: subtraction can round below the boundary and defer a whole poll. Pin an exact fixture epoch, not longer budgets; prove inherited failures on the unchanged base.

- For bounded readers that repeatedly return empty partial results, distinguish per-query exhaustion from shared quota exhaustion before raising any cap. Test eventual service across polls, including new arrivals and completed/disappearing cursor anchors: a fixed newest-first window can starve older active work forever. Preserve incomplete-coverage semantics and verify useful rows from the actual post-deploy collector, not just direct API probes; a concurrent maintenance stop makes that live check inconclusive.

- Isolate enrollment shell fixtures' HOME/PATH and refuse network Git. Diagnose login failures with a profile marker, bash trace and final HOME/PATH from the failing CI harness; local or simulated leaks are not CI proof. Mirror core's init sourcing with paths resolved before login bash can change HOME, then verify the repair in CI.

- Before exercising fork/timeout cleanup, validate exact integer PID ownership at the signal boundary and mock destructive signal/exit calls locally in unit tests. A bare fork mock can coerce to PID 1 and turn killpg into a broad user-process signal; a systemd scope alone does not contain that syscall. Use module-local process-control aliases rather than patching shared os functions, refuse external SIGCHLD reapers, and preserve unreaped-child identity through signalling. Test invalid/coercible IDs and lost-child ownership without real signals; real timeout integration must restrict signals to its own child.

- Inject receipt stalls at the exact append, not metadata POSTs; assert call return/raise, not renamed logs. Synchronize profiler sampling with RPC.

- Verify recurrence evidence delivery separately from incident dedupe: a create transport returning an existing open card proves neither that the new occurrence was appended nor that it is readable. Exercise actual handlers on a disposable board, assert full body/comment evidence before local ACK, and test a receipt-write crash followed by incident completion before retry. Earlier receipts from an unverified protocol must not become trusted ACKs merely through migration.

- Run real-handler lifecycle proofs in the CI fixture environment before review, not only under an installed launcher. Use the suite interpreter, explicit selected core and disposable storage when CI supplies source and dependencies but no launcher; prove the old failure under a launcher-free PATH and retain every transport/readback assertion. Local deployed-runtime success does not establish CI execution.

- Writing the chain after the fix is chosen, so the chain is built to justify it. Do the chain first; let it pick
  the fix.
- Answers that restate the question ("it failed because it errored"). Each why must add new information.
- Stopping at the first fact that sounds technical. "The pump shaft was worn" is a fact, not the root.
- Going so deep that the root is outside anyone's control ("because physics", "because the company was underfunded").
  Back up to the last level you can change.
- For a live failed-result observer, inspect the entire restart/continuation event chain before treating it as a process hang. A modern restart receipt can cease to cover the history after a successor binding; if that worker dies without its own result, strict attribution may veto both the observer and legacy expiry. Compare native claim/PID fields with replica projections, preserve real receipts/checkpoints, and reproduce restart → rebind → loss with distinct process identities. Measure admission using the host's enabled policy; an out-of-gateway cgroup census need not equal row-based admission.

- For state machines that add health evidence, trace the new field through every persistence projection: successful commit, failed-action observation allowlist, multi-slot quiet updates, and dry-run shadow load/save. A decision-only regression can pass while a sibling write failure discards an unhealthy reset and the next real tick acts on pre-interruption evidence. Exercise real ticks against the ledger with fake transports; distinguish invalidation (must survive failure) from evidence consumption tied to an uncommitted lifecycle transition (must not survive failure or preview).

- Checking for a parallel fix only during diagnosis. Right before you implement, fetch the repo, read its branch log
  since the incident and search open cards on every board for the same symptom: another session reading the same
  logs often lands the fix first. If one did, run your tests against its commit instead of pushing a second fix.

For native recovery proofs, distinguish stdio `tui_gateway.entry` from the web backend: the web lifespan starts the startup-resume sweep; a stdio process restart alone does not exercise it. Test supported `session.resume` on the original stored ID for client-reconnect coverage and report that separately from automatic boot recovery. Internal handler parameters may be refused by the external RPC schema. Decode `record_json` before counting child promises/attempts, and verify a preassigned delegation ID actually has a ledger row: capacity fallback can retain the ID without successful async admission.

When testing asynchronous lifecycle behavior, read `references/async-lifecycle-test-pitfalls.md` (startup cancellation, bootstrap, React, starvation, downloads, dispatch and negative assertions).

When attributing CPU or worker activity, read `references/resource-observability-pitfalls.md` (macOS thread accounting and worker identity independent of retained task rows).

For pmset sleep/wake attribution, match the event-kind column, not Sleep/Wake words anywhere in the line: `Kernel Client Acks` diagnostics can contain both `Delays to Wake notifications` and `Delays to Sleep notifications` after a real Wake. Preserve the raw matching line and preceding actual transition before treating an awake-gate rejection as proof the Mac is asleep; fixtures must include these acknowledgement lines.

When diagnosing SQLite stalls or corruption, read `references/sqlite-recovery-pitfalls.md` (transaction-lock reproduction and coordinated database recovery).

- Fixing the copy you can reach when another copy is the source of truth (a mirrored dir, a synced host, a vendored
  file). The next sync silently reverts the fix. Before editing, check what syncs into the file (rsync jobs, mtimes
  with whole-second precision, a "mirror" note). Land the fix at the source, or hand it to whoever owns the source.
  Report it as not done until it's there.

When an execute_code traceback raises an alert, read `references/traceback-attribution.md` (decoded envelopes, originating cells and core-versus-stdlib attribution).

- Closing a "not a bug" verdict with no why-chain. If the same false alarm keeps getting filed, the filer, tagger or
  classifier is the bug.
- Reading absence or a first sighting off the current log file only. A busy agent.log rotates about hourly, so
  "logged nothing from 00:20 to 01:10" or "first seen at 01:19" can be the rotation, not the event. Before
  claiming either, list the rotated files (`ls -l agent.log*`), grep them all in time order, and quote file and
  line. A card filed on such a claim gets a wrong title and points its fix at the wrong component.

