# Native macOS awake checks and accurate sync-round accounting

Mirror entries: 5307. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

macOS awake checks use current CoreFoundation/IOKit power assertions, power source and lid state without spawning per-round power commands when the native ABI is available. ABI unavailability retains the old command-line fallback; native read errors follow the existing conservative awake/error policy without a fallback fork. Sync-round accounting respects its scope toggle and attributes exceptions to the operation that raised them.

## How it works

Only ABI bindings are cached, never the awake verdict. The policy is active user OR AC power with lid open. Native copy/create references are released, borrowed Get values are not; missing optional assertion/clamshell properties retain command-policy behavior. `Scope('sync.round/loop')` is entered only when enabled. Context teardown records the operation's actual exception tuple rather than an unrelated ambient handled exception.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `cron/macos_awake.py`
- `cron/restart_rerun.py`
- `hermes_cli/kanban_sync_cpu.py`
- `hermes_cli/loop_accounting.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source tests simulate thirty rounds without spawning, changing native verdicts, native errors and unavailable-ABI subprocess fallback. Native ABI fixtures cover policy combinations, reference release, missing optional properties and binding-only caching. A Darwin-only actual-native-read test exists; it is not executed by this mirror task. Accounting tests cover executor residual publication once, ambient-except success, true operation errors and toggle/exception cleanup, with sync CPU/replica-age expectations updated.

Relevant source suites/fixtures:

- `tests/cron/test_awake_native_route.py`
- `tests/cron/test_macos_awake.py`
- `tests/cron/test_restart_rerun.py`
- `tests/hermes_cli/test_kanban_sync_cpu.py`
- `tests/hermes_cli/test_kanban_sync_replica_age.py`
- `tests/hermes_cli/test_loop_accounting_completion.py`
- `tests/hermes_cli/test_loop_accounting_sync_round.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

Native APIs are macOS-specific; no live macOS power read or performance benchmark was run here. A nonmacOS mirror cannot establish the Darwin-only case passed. The disabled accounting scope should not reintroduce instrumentation work.

## Why core instead of a plugin

Core cron awake policy and sync accounting surround the hot loop itself. A plugin cannot remove forks already performed by those core probes or fix context-manager exception attribution afterward.
