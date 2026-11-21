# Out-of-band dead sync-watcher detection

Mirror entries: 5086, 5087, 5088, 5089, 5095, 5096.

## Behavior

- Dispatcher checks can report a crashed sync loop after five minutes through the hub courier, avoiding the dead local sync outbox.
- A live loop or sync-lock holder suppresses the alert. Graceful STOPPED and schema-ahead conditions belong to other alarms.
- Alert state deduplicates crash/respawn cycles, comments on an open repair card, backs off failed courier operations, and avoids duplicate creates while an earlier create is unresolved.

## How it works

The outbox-age tick calls the dead-watcher detector even when its ordinary throttle skips other work. The detector first reads cheap crash state, checks local/other-process liveness, and only then performs update/transport work. `loop_running_here()` exposes core loop lifecycle without private-state inspection.

An incident retains its first-seen-dead identity until crash state clears, regardless of changing exception text. Failed tasks/create/comment operations record attempt/error state and retry after ten minutes. A pending create is waited on for up to six hours; rejected/expired/failed requests permit a fresh create. Open cards receive comments rather than parallel repairs. On the hub gateway itself the detector logs/persists an error instead of courier-filing onto its own sync-gated board.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_cli/kanban_sync_outbox_age.py`
- `hermes_cli/kanban_sync_watcher_alert.py`
- `hermes_cli/kanban_sync_claims.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Watcher-alert tests cover threshold edges, exact courier argv with no local create, tick/throttle composition, live-loop/lock suppression, incident reset and dedup, ten-minute failure backoff, gateway identity, hub self-suppression, public loop accessor, unresolved create expiry, and terminal courier rejection/expiry/failure. Real courier delivery, natural dispatcher alerting and live hub operations are untested here.

Source test/coverage paths:

- `tests/hermes_cli/test_kanban_sync_watcher_alert.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

Known gap: the hub's own dead sync still needs a non-Kanban notification route. This detector is not receipt repair and does not restart the gateway. Courier acceptance is not immediate card visibility; unresolved requests must remain pending rather than trigger another create.

## Why core instead of a plugin

The detector depends on core sync health records, dispatcher cadence, lock ownership and courier request custody; it must keep working precisely when the normal sync transport is unavailable.
