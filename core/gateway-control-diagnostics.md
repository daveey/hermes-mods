# Attribute control-request latency and blocked event loops

Patches **1524–1526**.

## Behavior

- Slow control requests log the allowlisted verb, executor queue wait, handler runtime, total time
  and in-flight count. The warning threshold is 0.5 seconds for total time or queue wait.
- Total time begins at the asyncio connection callback, not kernel accept, and ends at drain or
  completion. A cancelled connection whose Python handler still runs remains in flight until that
  handler finishes; accounting reports once.
- A watchdog samples loop-owned task stacks every 0.25 seconds and warns on heartbeat delay over
  one second, at most once per 30 seconds. Reports include live thread locations plus the last
  loop-owned task snapshot and its age, not request arguments, source lines, locals or task names.
- Optional watchdog construction/start failure leaves an already-bound listener serving. Snapshot
  failure still refreshes liveness and labels the old snapshot historical or unavailable; only
  exception class names are retained in this diagnostic path.
- Parser `RecursionError` while extracting the diagnostic verb becomes `unknown`, so diagnostics
  do not prevent the ordinary request handler from replying. Pending connection tasks have strong
  references until completion.

## How it works

`ControlDiagnostics`/`RequestTiming` coordinate callback acceptance, executor enqueue/run, drain
and cancellation under locks. `GatewayControlServer` submits to the existing control executor and
retains connection tasks. `LoopWatchdog` captures asyncio stacks only on their owning loop, while a
context-aware daemon thread checks heartbeat delay and reads live thread frames. Startup of the
watchdog is best-effort and separate from socket binding; stop cancels its heartbeat and joins it.

## Code

- `gateway/control_diagnostics.py`: timing, safe verb parsing, sanitized stacks and watchdog.
- `gateway/control_socket.py`: connection lifecycle and best-effort diagnostic startup.

## Tests

Source coverage; not executed in this documentation pass:

- `tests/gateway/test_control_diagnostics.py`: slow real verb, saturated executor queue, quiet fast
  request, callback delay, cancellation before start/while queued/while executing, unknown-verb
  redaction, watchdog rate limit, live blocked-loop stacks and teardown, failed bind without watchdog.
- `tests/gateway/test_control_diagnostics_lifecycle.py`: constructor/thread-start failure leaves
  listener usable; initial/later snapshot failures keep heartbeat and mark snapshot age; strong
  task retention; `test_verb_metadata_recursion_error_still_replies` injects parser failure and
  asserts the real connection still receives its response.
- No live production load or Windows transport parity is certified here.

## Applying

Apply the complete ordered mirror using the [README replay recipe](README.md#applying). Keep
1525/1526 with 1524: they isolate diagnostic failures from the service being diagnosed.

## Gotchas

These are attribution signals, not a new recovery mechanism or latency SLA. Callback acceptance
excludes pre-callback kernel/loop waiting. The watchdog thread needs the GIL to run; a native call
that never releases it can prevent observation. Task snapshots may be older than live thread stacks.
The existing long-timeout liveness watchdog is unchanged.

## Plugin?

No. This instruments core control-socket acceptance, executor work and event-loop lifecycle.
