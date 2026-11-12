# Machine metrics: tokens and sessions throughput counters

Patches: **2572**.

## Behavior

- The machine metrics report to the Board now carries four cumulative counters summed over every `state.db` on the host (the installation root's and each `profiles/*/state.db`): `tokens_io_total` (input+output), `tokens_cache_total` (cache read+write), `sessions_started_total` (`count(*)` of `sessions`) and `sessions_ended_total` (`count(ended_at)`).
- Token totals come from `session_model_usage`, which also includes auxiliary calls (compression, titles, approval, coach ...) that `sessions` never counts (about 12% of the hub's tokens per the commit message). A database without that table falls back to the `sessions` token columns.
- If any database is unreadable, or none exists, all four fields are omitted, never reported as 0 (a partial sum would read as a drop, 0 as a reset).
- Totals fall when sessions are pruned; a consumer is expected to treat that as a reset.
- A Board that predates the counters rejects the report with 400 `unknown field(s)`. The sender then retries once without the counters and the CPU busy fields (the CPU sample is never resent), keeping drain fields, and stops sending counters for `UNSUPPORTED_RETRY_S` (600 s per the test name). Any other 400 is not treated as an old Board.

## How it works

- `hermes_cli/machine_throughput_metrics.py` (new): `state_db_paths`, `_read` (read-only URI connection, 1 s busy timeout), `counters` (dedupes by `(dev, ino)`, returns `{}` on any `OSError`/`sqlite3.Error`), `cached_counters` (60 s cache, success or failure) and `reset_cache`.
- `machine_metrics.collect()` merges `cached_counters()` into the sample.
- `machine_drain_metrics.send_compatible(send, sample, on_counters_rejected=None)` implements the 400 retry; `Reporter` sets `counters_off_until` from the callback and strips the counter fields from samples until then.

## Code

`hermes_cli/machine_throughput_metrics.py` (new), `hermes_cli/machine_metrics.py`, `hermes_cli/machine_drain_metrics.py`, `tests/hermes_cli/test_machine_metrics.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_machine_metrics.py`. Entry path: `machine_throughput_metrics.counters` / `cached_counters` over real temp `state.db` files, `machine_metrics.collect`, and `send_compatible` / the reporter with a fake send. Covered: summing root and profiles, auxiliary usage rows, omission on an unreadable db or no db, the 60 s cache, a 22k-session timing guard (< 0.5 s), the old-Board retry with the 600 s back-off, and non-counter 400s. A live Board and a real network send are **untested**; the Board-side panel is outside this repo.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The Board must accept the four fields; an older one costs one rejected request, then back-off.
- A counter drop (prune, deleted profile) is indistinguishable from a reset by design.
- Results are cached for 60 s, so new sessions appear in the counters with that lag.

## Plugin

Core change; no plugin required.
