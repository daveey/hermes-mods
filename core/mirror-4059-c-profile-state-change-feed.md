# Profile-specific state topics and race-safe subscription seeding

Mirrored entries: 3863, 3893.

## Behavior

Each change-feed subscriber using the bare state alias watches its own profile’s state database, not the profile captured by whichever caller started the feed. Only subscribed state databases are probed; unreadable ones wake their subscribers. The infrastructure probe thread inherits no caller profile/work scope.

A subscriber added while a probe is in flight retains its seed and does not receive spurious wakeups merely because that probe did not read its new topic.

## How it works

`subscribe` resolves state into `state:<absolute path>` in the calling context and reads an initial key immediately. `poll_once` obtains per-topic state keys separately from host-wide board/replica keys. `start` builds a passive thread inside an empty contextvars.Context so active work accounting and profile context do not leak.

The follow-up stores seeds separately before the first baseline exists. Under the commit lock, a poll merges seeds for currently subscribed topics missing from that poll’s snapshot, from either pending seeds or the previous key map; it then clears the seed map. This preserves a mid-probe subscription without inventing a change.

## Code

- `hermes_cli/host_change_feed.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/hermes_cli/test_host_change_feed.py`: Creates subscribers in two profile contexts and checks only the matching database change wakes each; checks baseline/no-subscriber behavior, unreadable-state wake and detached thread context/work accounting. Follow-up tests subscribe mid-read before and after the first baseline and assert the seed survives with no subsequent false wake.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

The bare state topic is a subscribe-time alias, not a process-wide database counter. This is change notification rather than a database snapshot or delivery receipt. An unreadable state probe intentionally causes wakeups instead of silently claiming no change.

## Plugin?

No. Process infrastructure must resolve caller context at subscription and own its own thread lifecycle.
