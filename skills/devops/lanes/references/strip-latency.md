# Lanes strip slow / timing out in Desktop

The user's wording of the error is usually a paraphrase. Do not grep the trees for it: the strip
shows an error when `desktop-plugins/lanes/plugin.js` gives up on `GET /v1/lanes` after
`REQUEST_TIMEOUT_MS` (20 s). Go straight to measuring.

## 1. Time the served endpoint (hub)

```python
import time, pathlib, urllib.request
h = {"X-Hermes-Session-Token": (pathlib.Path.home()/".hermes/desktop-ssh/always-on/token").read_text().strip()}
for p in ["/api/status", "/api/plugins/lanes/v1/lanes?hours=6", "/api/plugins/lanes/v1/lanes?hours=6"]:
    s = time.monotonic(); urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:9147"+p, headers=h), timeout=60).read()
    print(p, round(time.monotonic()-s, 1))
```

Run a few calls in one foreground command (no sleep loops). `/api/status` is the baseline for
whole-process load.

## 2. Separate compute cost from serve contention

Load `plugins/lanes/dashboard/plugin_api.py` with `importlib.util.spec_from_file_location` under
`~/.hermes/hermes-agent/.venv/bin/python` (with that tree on `sys.path`). Call
`m.lane_view(6.0, '6h', False)` several times and print `m._observation_cursor()` before and after
each call. Wrap a single cold call in `cProfile` and sort by cumulative time.

- Standalone takes seconds but served takes tens of seconds: the serve process is contended. Check
  `top` on the routed backend PID and the load average, and look for an open hub-serve load card
  before filing a new one.
- A fast warm call but a slow cold one: the cache is not surviving. `_refresh_cursor` clears
  `_cache`/`_flow_cache` on every semantic board revision, so on a busy board nearly every GET is a
  cold recompute.

## 2b. Chips missing, "Partial", "not live" (filter counts broken)

The Sessions-tab filter chips (`all`, colour squares, dashed no-cards count) and the LANES/GOVERNORS
summary are `desktop-plugins/lanes/plugin.js` (`StatusTotals`, `SessionsTabTotals`), not core
`apps/desktop/src/app/chat/sidebar`; start there. Only `all` plus a dashed count equal to the whole
list means the view's `status` is `{}` and `sessions` is empty, so every chat counts as no-cards.

1. GET `/v1/lanes?hours=6&window=6h&expanded=true` on the routed backend (the 9148/9147 recipe above;
   no token = 401). Print `flow_complete`, `summary_complete`, `status`, `len(sessions)`.
2. Probe the stream: `websockets.connect(ws://127.0.0.1:<port>/api/plugins/lanes/v1/lanes/stream?hours=6&window=6h&expanded=true&token=<token>)`,
   send the params JSON, read three frames. A close with 1013 is why Desktop shows "not live" and
   falls back to the 60 s REST poll, which computes in the (often GIL-starved) backend process.
3. `grep "lanes board_merge deadline expired" ~/.hermes/logs/errors.log`: `wall` far above
   `thread_cpu` means lock starvation in the serve process, not a slow read.
4. Compare `plugins/lanes/dashboard/*.py` mtimes (and core `hermes_cli/kanban*.py`) with the backend's
   `ps -o lstart=`. The projector child is fenced by `projector_identity` captured at load, so a
   plugin sync after backend start likely disables the stream until the dashboard restarts. Standalone
   `m._projector()._spawn(m._projector()._configuration())` with current code tells a code fault from
   that fence.

A standalone `_lane_view` that completes proves the code, not the served view; acceptance is the
chips on the routed backend.

When `test_projector.py` raises `lanes projector startup failed`, see the child's stderr by making
`projector_client.Process._drain` write to `sys.__stderr__` in a throwaway worktree (outside
`~/.hermes/cache/scratch`). `runtime_command` with a fresh temp HERMES_HOME prints "preparing
dependencies for this data root" and misses the 5 s ready wait: that is test setup, not the
production failure.

## 3. Do not just raise the client timeout

A longer timeout hides the latency. The fix belongs on the hub request path: serve the last view
while a recompute runs, or cut the cold cost.

## 4. Verify stale-while-revalidate separately from cold-cost changes

Keep completed REST views keyed by the full `(hours, window, expanded)` tuple across semantic
revision invalidation. Allow only one refresh per key; never borrow another key's result. Preserve
synchronous computation for callers that need it. Test that a GET returns the previous view while a
refresh is deliberately blocked, and that a cold different key cannot receive that view.

Read `stale_seconds` as snapshot age, not request duration. After a polling gap the first response can
be old and still fast: a subsequent age drop proves a completed refresh. Record both age and latency;
check revision or current card evidence separately when freshness is in question.

For a five-minute latency proof, retain every request's timezone-aware start, HTTP status, duration,
age and normal load. Space 20 starts across at least 300 seconds, not 20 intervals ending short of
five minutes. Compute nearest-rank p95 from sorted raw durations and validate count, elapsed span,
all-success responses and the maximum. Keep raw evidence off disposable workers before parking.

Verify plugin activation, installed core ancestry and routed backend boot identity separately. A
warm roller can retain the working old backend after a replacement fails startup; checkout ancestry
and a fresh gateway do not prove the routed serve loaded a parser/import optimization. Read active
route metadata and the selected backend's health/boot evidence; preserve natural-reload restrictions
and report any activation gap rather than force-restarting a working backend.
