# Delivery queue timestamp race settling

Mirror snapshot 4059; covered ordinals: 3918, 3937.

## Behavior

A future queue-file timestamp causes a conservative first-sight recheck but no longer keeps every unchanged poll draining until wall time catches up. Recent timestamps, including age exactly one granule, still owe their recheck, using the newest main database or WAL timestamp.

## How it works

`gateway/run_delivery_queue_watch.py:DeliveryQueueWatch` passes a `first_sight` flag to `_is_racy`. `changed` computes it from the whole observed stamp. A future timestamp is racy only on first observation; a nonnegative age within the inclusive granularity window remains racy. The remembered prior racy state still causes the next recheck even if current time has already advanced past the boundary.

## Code

- `gateway/run_delivery_queue_watch.py`

### Mirrored commits

- 3918: `839d2087294ab9bc3ab41fe3463ec6cfe3068b50` — Settles future delivery timestamps after a first-sight recheck.
- 3937: `5a179c952938d43dc343a4b15f6e85318363f8ef` — Pins newest queue mtime selection and inclusive granule boundaries.

## Tests

- `tests/gateway/test_cron_delivery_housekeeping.py`: a same-size rewrite preserving a future timestamp wakes once then settles; a newly changed future stamp wakes/rechecks then settles again. Separate main-file/WAL cases prove an older companion cannot hide a same-stamp write to the newest file. Integer-clock boundary cases distinguish age exactly one granule from one nanosecond beyond it and assert subsequent stable skips.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

Timestamp identity is a conservative change signal, not proof that a delivery completed. Future timestamps may arise from clock rollback or writer skew. The tests use controlled clocks and file metadata; they do not benchmark a live network filesystem or prove live delivery.

## Plugin?

No. The core queue poll must own the remembered timestamp and recheck obligation.
