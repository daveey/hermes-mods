# Host change-feed contracts and producer-admission/PID read gates

Mirror snapshot 4059; covered ordinals: 3923, 3924, 3930, 3931.

## Behavior

- Test-only host-feed additions pin topic filtering, nonblocking waiter behavior, unreadable-state semantics, and passive probe-thread ownership.
- Producer-admission sweeps keep read-only observers and skip a store only when unchanged since a pass proving no pending owner. Pending rows retry each pass even without a commit.
- Profile-list PID probes reuse answers while PID/lock signatures and process identity remain valid, with a thirty-second backstop.
- Kept admission observers are released for profile delete/restore, lease loss, and sweeper shutdown.

## How it works

`tui_gateway/producer_admission.py` observes file identity and `PRAGMA data_version`, reads pending owners cheaply, and falls back to the full open on observer failure. An empty observer result needs no full SessionDB open. `_record` accepts a completed-pass key only while its exact observer connection is still kept: a new connection restarts its data-version space. `release_under` integrates with `hermes_state_registry` close operations.

`gateway/status_pid_memo.py` keys by PID path and probe identity, stats PID/lock files, rechecks live PID start identity and zombie/dead states, and bounds reuse by `RECHECK_S`. Linux uses procfs; the portable path uses existence, start time, and lock evidence. `hermes_cli/profiles.py` uses this noncleaning memo as one rung of its existing liveness resolution.

## Code

- `gateway/status_pid_memo.py`
- `hermes_cli/profiles.py`
- `tui_gateway/producer_admission.py`
- `hermes_state_registry.py`

### Mirrored commits

- 3923: `7a4f725146c6b99adbc908b6dd9c3ad9f33c0485` — Pins change-feed topic, waiter, UNKNOWN, and thread contracts.
- 3924: `56085c371f74b9691487da97f259d99c29d98f04` — Pins independent state-probe failure handling and passive threads.
- 3930: `38350ed5b36263d9e77473f98fcb9e741ac951d9` — Gates producer-admission reads and memoizes profile PID probes.
- 3931: `23df41d49f08455c0fb5bb376fe3100e1fe2a657` — Releases observers on lifecycle changes and fences remembered keys.

## Tests

- `tests/hermes_cli/test_host_change_feed.py`: stopped waits return promptly; pending async results avoid waiter registration; empty wakes re-arm/yield instead of spinning. Subscribers see only matching topics, exact patterns are not prefixes, repeated start keeps one thread, unreadable boards/state are UNKNOWN, and a replaced replica database is reopened.
- `tests/hermes_cli/test_host_change_feed_state_probe.py`: repeated unreadable state wakes every time, subscribe seeds UNKNOWN, probe threads are passive even under a work scope, and whole-source failure neither overwrites independently read state keys nor resurrects closed subscriptions.
- `tests/tui_gateway/test_producer_admission_gate.py`: unchanged quiet stores avoid full opens; unrelated commits can be answered by observers; new admissions and unchanged pending refusals retry. Replaced/deleted/added profiles and observer failures take the required paths. Registry release closes only matching handles, then reopens successfully; dropped mid-pass observers cannot record stale keys; lease loss closes them. A replay budget case measures synthetic all-profile commit traffic.
- `tests/gateway/test_status_pid_memo.py`: unchanged files/live identity reuse a probe; PID/lock changes, death, PID reuse, zombies, and deadline expiration reprobe. Racy file reads are not memoized and probe identities do not share answers. Profile-list integration and synthetic CPU-budget assertions are included; the follow-up unwraps test I/O guards only for that benchmark.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

Unknown is not the same as absent or unchanged. `data_version` keys cannot outlive their connection. A pending refused admission must not wait for another write. Only noncleaning PID probes are memoized; this is not a cache for calls that unlink stale files. Performance tests describe synthetic budgets, not measured deployment savings from this pass.

## Plugin?

No. Passive feed contracts and connection/PID gate ownership require core lifecycle integration.
