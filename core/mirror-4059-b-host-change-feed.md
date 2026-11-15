# Process-wide host change feed for subscribed loops

Patches: 3833.

## Behavior

Adds a shared per-process probe feed that can wake only loops subscribed to changed board, replica or state counters, while preserving each caller's own timed wake deadline. This patch supplies the facility; it does not migrate every existing loop to it.

## How it works

`HostChangeFeed` starts one daemon probe thread on first subscription. Exact and prefix topics feed coalesced pending sets to condition-variable or async-event waiters. The first probe establishes a baseline; later probes compare topic keys, including appearance/disappearance. Unknown counters never compare equal, so unreadability repeatedly wakes known-topic subscribers instead of suppressing recovery.

Default keys are each local board's content revision, the replica's cursor plus file identity, and state DB data-version fingerprints from a retained observer. Read-only replica connections reopen on device/inode replacement. Probes are `change_feed.probe` accounting passes with skips/changes and moved-topic row counts. The singleton suppresses autostart under pytest; reset stops it and closes retained connections.

## Code

- `hermes_cli/host_change_feed.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/hermes_cli/test_host_change_feed.py` (patch 3833).

- **3833:** Feed tests cover first baseline, topic-specific/thread/async wakes, deadlines, coalescing, unknown probes, topic appearance/disappearance, stop/close, probe accounting, replica cursor-only keys, state external commits, default counter inventory and pytest singleton non-autostart.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

A subscriber must still run its first pass and retain its timed work; the baseline deliberately emits no wake. This is one thread per process, not one daemon for the entire machine. A failing probe marks previously known topics unknown; it cannot discover names it never observed. Tests do not prove all downstream loops have adopted this API.

## Plugin?

Potential consumers could use an API, but this implementation belongs in core because it reads core board/replica/state counters and participates in core loop accounting.
