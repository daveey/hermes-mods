# Test range server is threaded so paused downloads stop in 0.04 s

Patch: 0694 (daveey/hermes `a081ba9470`).

## Behavior

`test_quickstart_pause_stops_the_sequence` flaked on main CI (run 40653617896, slice 2: `running == paused`, passed on retry). Test-only change; no product behavior changes.

## How it works

The shared `dl_server` in `tests/pm/_range_server.py` was a single-threaded `HTTPServer` (backlog 5) while the downloader opens 8 range connections, so a paused job waited for every queued connection to drain. Pause-to-paused measured a steady 3.66 s on the hub, a quarter of the tests' 15 s poll deadline. It is now a `ThreadingHTTPServer`, which brings it to 0.04 s (figures from the commit message, not re-measured here).

## Code

`tests/pm/_range_server.py`.

## Tests

No new test: the change is to a test helper. Per the commit message, the 22 files using the server pass (202 tests). This mirror did not run them (untested here).

## Applying

`git am -3` 0694 in series order.

## Gotchas

- Handlers now run concurrently; a test that relied on serialized requests would need its own lock.

## Plugin?

No.
