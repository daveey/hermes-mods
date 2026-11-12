# pm client reports a transport failure when its worker dies before the request

Patches: **2541**.

## Behavior

The pm client wrote its request to a worker process that may already have exited. On a starved runner the worker's exit could beat the write, so `flush()` raised a raw `BrokenPipeError` (raised again when `Popen.__exit__` closed stdin) instead of `InstallError("worker exited without a result")`. Now an early worker death is reported as the transport failure `InstallError`, deterministically.

## How it works

- `pm/client.py` `_request`: the inner `send()` treats `BrokenPipeError` on write/flush as "worker gone" and returns; the worker's stdout EOF then produces the missing-result error.
- The `finally` block closes the writer itself (ignoring `BrokenPipeError`) before terminating the process, so `Popen.__exit__` has no refused buffer to re-flush.
- Related: [pm-target-gap-no-worker.md](pm-target-gap-no-worker.md).

## Code

`pm/client.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/pm/test_worker.py::test_worker_death_reports_transport_failure`, now parametrized with a deterministic `exits-before-request` variant (a `Popen` subclass waits for the worker to exit before the first write). Covered entry path: the pm client `ensure(...)` call against a worker that exits immediately; other operations and the pause/cancel writer path are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Any `BrokenPipeError` on a request write is swallowed by design; a failure that still has output is reported only through the worker's stdout/exit handling.

## Plugin

Core change; no plugin required.
