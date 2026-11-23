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

## 3. Do not just raise the client timeout

A longer timeout hides the latency. The fix belongs on the hub request path: serve the last view
while a recompute runs, or cut the cold cost.
