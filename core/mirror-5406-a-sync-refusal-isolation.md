# Per-board containment refusal backoff and supervised sync recovery

Mirror entries: 5082, 5083, 5084, 5085.

## Behavior

- A containment refusal ends only that board's sync round; healthy boards continue and the refused page is not applied.
- Refused boards back off from 60 seconds, doubling to a 15-minute cap, retaining the cursor at the last good page and logging each distinct refusal.
- The sync watcher has a slow supervised retry after its rapid restart budget is exhausted; shutdown suppresses that respawn.

## How it works

`kanban_sync_refusal.py` tracks board-specific holds, retry deadlines and distinct error text. `sync_all_once` catches the typed refusal at the board boundary. `run_forever` removes held boards from both selection and wake-ready sets instead of classifying them as busy/skipped work.

The supervisor gives the sync watcher a 300-second slow retry tier after its existing give-up hook, with a fresh restart budget. Its waiting task is marked supervised so scale-to-zero ignores it. Other watcher classes retain their existing give-up behavior.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `gateway/run_adapters.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_refusal.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Sync-refusal tests cover healthy-board progress, unchanged cursor semantics, distinct-error logging, capped backoff and the actual `run_forever` loop: no busy retry while held and selection resumes after expiry. Supervisor tests cover recovery after give-up, unchanged unlisted watchers and shutdown before/during slow wait. Real network refusals and running gateway recovery are untested here.

Source test/coverage paths:

- `tests/gateway/test_supervised_slow_retry.py`
- `tests/hermes_cli/test_kanban_sync_refusal.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

This contains failure; it does not relax canonical certification or fix the refused evidence. A held board remains stale until valid evidence arrives. Slow restart is deliberately scoped, not an unlimited retry policy for every supervised task.

## Why core instead of a plugin

Board scheduling, cursor custody and watcher supervision are core services. A plugin callback cannot safely catch a failed page after it has escaped the sync loop or control supervisor task accounting.
