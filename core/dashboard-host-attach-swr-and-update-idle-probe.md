# Dashboard host-gateway stale-while-revalidate and update idle-probe fix

Patches: 1215 1216 1218 1219 1220.

## Behavior

- A dashboard process no longer pays a slow gateway `identify` (control-socket client timeout, ~2 s) on every request. Once the host-gateway memo expires, the dashboard serves the expired value for up to 30 s (`HOST_GATEWAY_MAX_STALE_S`) while one background thread re-probes. Older than 30 s, the probe is synchronous again. (1218)
- CLI and lifecycle processes do not opt in and keep the synchronous TTL behaviour. Record writes still invalidate the memo eagerly in every process. (1218)
- Control-socket verbs run on their own 4-thread pool, so an `identify` is not queued behind other `run_in_executor(None, ...)` work in a healthy gateway. (1218)
- `hermes update` waiting for a backend's background review no longer treats an unanswered idle probe as "review ended". While the pid is alive, no answer means keep waiting; the existing 900 s deadline still bounds the wait. The probe HTTP timeout goes from 3 s to 10 s. Before, a CPU-starved backend that missed the timeout was stopped unfenced mid-review (no commit before SIGTERM). (1215)
- 1216 makes the liveness check use `psutil.pid_exists`. 1219 and 1220 change tests / lint only, plus one product-code swap in 1220 (below).

## How it works

`gateway/host_attach.py`: module state `_stale_while_revalidate`, `_refresh_lock`, `_refresh_running`; `enable_stale_while_revalidate(enabled=True)` opts a process in. In `host_gateway()`, with `wait_for_channel <= 0` and a cached entry: age below `HOST_GATEWAY_CACHE_TTL_S` returns the memo; else if opted in and age below the cap, `_refresh_probe_in_background()` is called and the stale memo returned. `_refresh_probe_in_background` single-flights via `_refresh_running`, calls `_probe_host_gateway(0.0)`, and stores the result only if `_cached_probe` is still the same object it saw at start, so an invalidation during the probe is not overwritten. Exceptions are logged at debug. `hermes_cli/web_server_app.py::app_lifespan` calls `enable_stale_while_revalidate()` on entry and `(False)` in `finally`.

`gateway/control_socket.py`: lazily created `_CONTROL_EXECUTOR` (`ThreadPoolExecutor(max_workers=4, thread_name_prefix="control-socket")`) via `_control_executor()`, used where the request handler is dispatched.

`hermes_cli/dashboard_procs.py::_await_background_reviews`: after a pid has been waited on, `idle = _retirement_request(endpoint, None)`; if `idle is None and pid in waited and _pid_alive(pid)`, `continue`. `_pid_alive` was first `os.kill(pid, 0)` (1215), replaced by `psutil.pid_exists` (1216). `_REVIEW_RETIRE_HTTP_TIMEOUT_S = 10.0`.

1220: the background re-probe thread is started with `agent.memory_provider.spawn_context_thread` instead of a bare `threading.Thread`, to satisfy the repo's code-health gate (context propagation); test `time.sleep` calls carry `# health: allow HX013` markers.

## Code

- `gateway/host_attach.py` (1218, 1220)
- `gateway/control_socket.py` (1218)
- `hermes_cli/web_server_app.py` (1218)
- `hermes_cli/dashboard_procs.py` (1215, 1216)

## Tests

- 1215 `tests/hermes_cli/test_update_stale_dashboard_review.py`: `test_unanswered_idle_probe_while_waiting_is_not_taken_for_the_review_ending` (scripted `_retirement_request`, `_pid_alive` patched True; probe and prepare time out, wait continues, final commit sent). The dead-pid branch (`idle is None` and pid not alive falls through to the old path) is untested.
- 1216: no dedicated test. The new test patches `_pid_alive`, so `psutil.pid_exists` is untested.
- 1218 `tests/gateway/test_host_attach_swr.py` (new): `test_dashboard_mode_serves_stale_memo_while_reprobing`, `test_cli_mode_keeps_synchronous_probe`, `test_stale_memo_older_than_cap_is_reprobed_synchronously`, `test_control_socket_identify_not_queued_behind_default_executor`. The invalidate-during-probe guard and single-flight are untested.
- 1219 same file: `silent_gateway` fixture now uses a real accepting-but-silent AF_UNIX socket and the real client. Adds `test_profiles_http_requests_do_not_repay_silent_socket` (FastAPI `TestClient` on `/api/profiles`, summed `host_gateway` time < 0.5 s). The executor test is rewritten to saturate a 1-worker default executor and require `identify` to answer within 0.5 s.
- 1220: only the sleep markers; `spawn_context_thread` itself has no dedicated test.

## Applying

Apply the complete numbered series at the merge-base with `git am -3 --empty=keep`; patches are not independent. 1216 edits the `_pid_alive` that 1215 adds. 1219 and 1220 edit the test file 1218 creates, and 1220 edits the `host_attach.py` lines 1218 adds. 1215/1216 (update path) are independent of 1220-1222 in code, but are applied in the same series. See [README.md](README.md).

## Gotchas

- SWR is process-wide opt-in and reset when the app lifespan exits. Any new long-lived server needs its own `enable_stale_while_revalidate()` call.
- `host_gateway(wait_for_channel > 0)` bypasses the memo and SWR entirely.
- A dashboard can serve a gateway answer up to 30 s old; the first call in a cold process still pays the full probe.
- `_pid_alive` runs only for pids already in `waited`; a pid that never answered a first probe keeps the old behaviour.
- `psutil` is imported lazily inside `_pid_alive`.
- The 10 s probe timeout lengthens each poll when a backend is hung.

## Plugin?

No, core change.
