# Bounded replica reads and GIL budget in serve

Patches: **2453**.

## Behavior

On large Kanban replicas (thousands of task rows), the serve/dashboard backend could stall other threads: each fetched SQLite row released and reacquired the GIL, and under contention 12k rows could take on the order of 90 s. Replica task reads now cross the SQLite/Python boundary once per scan instead of once per task, and `hermes serve`/the dashboard backend applies the same interpreter switch-interval policy as the gateway. Results are unchanged, including per-row handling of malformed task JSON.

## How it works

Four replica scans (`kanban_session_activity._read_remote_links`, `tui_gateway.kanban_remote_notices._task_rows`, `tui_gateway.kanban_remote_observations._changed_rows_cards`, `tui_gateway.remote_index_projection._read_rows`) now run `SELECT json_group_array(json_array(...)) FROM replica WHERE coll = 'tasks'` and decode the one aggregated text value with `json.loads`. The `data` column is aggregated as raw text, not `json(data)`, so one malformed task cannot poison the whole read. Warm identity scans (seq only) remain proportional to changed rows for payload point reads; their one-envelope decode is why an observation test now expects two JSON decodes instead of one. `web_server.start_server` calls `gateway.gil_budget.apply_gateway_switch_interval()` before starting the runtime; the default is a 1 ms switch interval, `HERMES_GATEWAY_SWITCH_INTERVAL_MS` overrides it, and `0` leaves the interpreter default (5 ms). See [gateway-gil-and-trim.md](gateway-gil-and-trim.md) for the gateway policy it reuses.

## Code

`hermes_cli/kanban_session_activity.py`, `hermes_cli/web_server.py`, `tui_gateway/kanban_remote_notices.py`, `tui_gateway/kanban_remote_observations.py`, `tui_gateway/remote_index_projection.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tui_gateway/test_replica_bounded_reads.py` (12,000-row replicas with a fetch-counting connection: `_task_rows`, `_read_remote_links`, `_changed_rows_cards`, `_read_rows`; old and modern replica schemas), `tests/hermes_cli/test_serve_gil_budget.py` (`start_server` applies the switch interval before the runtime, default, override and disable values), plus adjusted `tests/hermes_cli/test_kanban_replica_read_memo.py` and `tests/tui_gateway/test_kanban_remote_observations.py`. The dashboard (`hermes dashboard`) entry path to `start_server` is untested here beyond the direct `start_server` call.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Needs SQLite's JSON functions (`json_group_array`, `json_array`).
- The aggregate builds the whole result in memory as one string before decoding.
- Switch-interval changes affect every thread in the serve process, not just replica reads.

## Plugin

Core change; no plugin required.
