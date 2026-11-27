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
   no token = 401). Record the exact response key list, `configured`, `warming`, `reason`,
   `flow_complete`, `summary_complete`, `status`, and `len(sessions)` alongside HTTP status and
   latency. Do not infer an absent warming flag from a probe that never printed it: a valid cold
   response `{configured: true, warming: true}` also prints `None None 0` for flow/status/session
   count. Distinguish explicit warming from an unflagged empty object before proposing a fix.
   For cache-expiry verification, preserve timezone-aware starts and monotonic start times,
   validate per-listener counts and spacing programmatically, and assert every zero-session
   response has `warming is True`.
2. Probe the stream: `websockets.connect(ws://127.0.0.1:<port>/api/plugins/lanes/v1/lanes/stream?hours=6&window=6h&expanded=true&token=<token>)`,
   send the params JSON, read three frames. A close with 1013 is why Desktop shows "not live" and
   falls back to the 60 s REST poll. With the current projector-enabled implementation, REST and
   default warming share the same child supervisor as the stream; child failure must not trigger
   expensive parent-process recomputation. Only an old core without projector support uses the
   bounded in-process fallback.
3. `grep "lanes board_merge deadline expired" ~/.hermes/logs/errors.log`: `wall` far above
   `thread_cpu` means lock starvation in the serve process, not a slow read.
4. Compare dashboard/core identity bytes and parent/child boot identities across a real sync.
   The supervisor now recaptures disk identity and replaces its child on compatible updates, without
   restarting the parent. Wire protocol VERSION mismatch still refuses, and the child checks its
   identity for every job. Cover deployment DURING an active request too: changing bytes before
   opening a stream misses both the child's per-job refusal and the parent's post-result fence.
   Keep the same future/revision and bounded newer-demand queue while retrying one changed
   generation; reject protocol mismatch and repeated movement rather than dropping either fence.
   Prove uninterrupted service on one socket across a byte-changing sync, not merely three complete
   frames from a new socket afterward. A pure mtime touch does not change the content-hash identity. On older
   code the captured router-load manifest permanently disabled streaming after an update.
5. If the child serves partial flow with `interrupted`, inspect every borrowed reader, including the
   fleet replica: resetting a local connection's phase budget does not reset the replica's handler.
   Projector readers now use the shared board CPU budget and bounded snapshot lifetime on both
   connections, not a leftover two-second whole-job wall deadline. Verify real local/fleet reads
   after a simulated three-second wait and refusal beyond the snapshot wall backstop.

Natural rollout can change the active port (for example 9148 to 9149) while the previous backend
remains alive to drain existing connections. Read `desktop-ssh/always-on/active.json` and verify
its selected PID in authenticated debug frames. Do not report the retired port as the new rollout.
Verify the explicitly requested listener as well as the active route: natural rotation may leave
old metadata for a retired port, so require a live listener and matching process birth identity.
Continuous live verification on the original listener kept one authenticated socket open across
a real dashboard-byte sync: the parent stayed unchanged, the child changed, and every frame
remained complete with nonempty status and sessions, including at least three after replacement.
Retain the bounded observer's raw frames and ready marker under unique filenames; preserve
failed attempts rather than overwrite them. A standalone new connection after sync is weaker
proof and must not be labelled uninterrupted recovery.

A standalone `_lane_view` that completes proves the code, not the served view; acceptance is the
chips on the routed backend.

Projector fixtures must use the already-prepared test interpreter and import paths while preserving
an isolated HERMES_HOME. Bootstrapping production `runtime_command` against a fresh temp home can
print "preparing dependencies" and miss the five-second ready bound; fix fixture setup, never skip
the tests or raise that timeout. The production runtime launcher remains unchanged.

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
