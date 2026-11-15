# Cron delivery test synchronization and racy queue stamps

Mirrored entries: 3869, 3870.

## Behavior

A queue-watch stamp observed within its filesystem timestamp granule cannot prove no change: the next lookup triggers a pass even if size and mtime match. Stable old stamps can settle normally.

Cron delivery tests explicitly wait until their event-loop thread is running, so slow scheduling cannot turn an intended in-flight send into an accidental standalone fallback.

## How it works

`DeliveryQueueWatch` records whether the previous db/sidecar stat tuple was racy using time.time_ns and a conservative one-second MTIME_GRANULARITY_NS. `changed` skips only an identical nonracy stamp; it always refreshes racy status after observing the current tuple. This catches same-size page rewrites whose mtime did not advance after a checkpoint removed the WAL.

Live-loop scheduler tests move out of the large scheduler test file. A callback scheduled on the loop sets an Event and the fixture waits for it before delivery begins; the unstarted-loop fallback remains a deliberately separate control.

## Code

- `gateway/run_delivery_queue_watch.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/cron/test_scheduler.py`: Removes the three live-loop delivery cases into the dedicated module without changing production scheduler code.
- `tests/cron/test_scheduler_live_loop_delivery.py`: Asserts an in-flight send outlasting the synchronous wait finishes once without cancellation, a never-started send falls back to standalone, and a partial multi-chunk delivery is not resent. The event-loop-ready Event establishes the intended fixture state.
- `tests/gateway/test_cron_delivery_housekeeping.py`: Pins wake-before-housekeeping and drain-settle behavior with controlled stamp age; a same-size same-mtime write still wakes twice while the previous observation is racy, then stable probes settle to false.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

The conservative granule can cause an extra empty pass; removing that pass can lose a delivery-state change. Stat tuples are a wake hint, not transaction identity. No cron delivery was sent by this documentation pass.

## Plugin?

No. Queue wake correctness and scheduler delivery fallback are core gateway behavior.
