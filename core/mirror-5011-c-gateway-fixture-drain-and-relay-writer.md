# Watch-notice drain wait and relay heartbeat writer serialization

Patches: **4554, 4596** (5011 snapshot).

## Behavior

Gateway watch-notice fixtures wait until every live drain from the setup turn has finished before patching schedule-to-noop and admitting a notice. Product drain and admission behavior is unchanged.

Relay multiplex heartbeat recovery fixtures mutate the changed connector through the existing bounded SessionDB writer mutex instead of a raw shared SQLite connection, so settlement cannot deadlock against the fixture.

## How it works

After the settle wait, `_gateway()` awaits every live drain task (bounded like settle) before patching `_schedule`. Drains re-read the FIFO after settling; an earlier fixture raced that re-read and claimed the admitted notice.

The heartbeat fixture uses `SessionDB._execute_write` for connector replacement, covering commit and authorizer teardown. A regression forbids raw connection access.

## Code

- `tests/gateway/test_watch_notification_reset_pin.py`
- `tests/gateway/relay/test_relay_multiplex_heartbeat_recovery.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/gateway/test_watch_notification_reset_pin.py`
- `tests/gateway/relay/test_relay_multiplex_heartbeat_recovery.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Patching `_schedule` does not stop a drain already running. A visible terminal row is not proof the settlement writer has detached its authorizer. No production gateway or relay behavior is claimed here.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
