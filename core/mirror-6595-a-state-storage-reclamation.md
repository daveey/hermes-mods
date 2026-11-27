# Backup snapshot pruning and settled-result compaction

Patches: 6022, 6023, 6024, 6065, 6066, 6067.

## Behavior

- Quick-snapshot pruning moves to `backup_prune`. A failed database capture still skips pruning entirely. A snapshot that skipped a database for size now prunes, but keeps older snapshots that actually contain a skipped database; the just-made snapshot is exempt by name and the keep count is floored at 1 in that case.
- Settled admissions past the retry horizon have their stored result compacted (final response, flags, usage kept; transcript copies dropped), at most 1000 rows per pass; results with empty or silence-marker replies are left whole. The pass runs inside `prune_sessions`, so a store that never prunes never compacts; rows already terminal before the `settled_at` column existed age from the first pass.

## How it works

backup_prune implements the prune rule; hermes_state_result_horizon runs from maintenance with a settled_at trigger stamp.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/backup.py`
- `hermes_cli/backup_prune.py`
- `hermes_state_common.py`
- `hermes_state_maintenance.py`
- `hermes_state_result_horizon.py`

## Tests

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/hermes_cli/test_backup.py`
- `tests/hermes_cli/test_backup_prune.py`
- `tests/hermes_state/test_result_horizon.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Failed capture keeps every older snapshot.
- Rows predating the column age from first stamp.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes backup and state maintenance inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
