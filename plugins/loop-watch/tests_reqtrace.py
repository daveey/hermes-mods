import asyncio
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("loop_watch_reqtrace_t", _here / "reqtrace.py")
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)


def _slow_handler_body(seconds):
    time.sleep(seconds)  # e.g. a sync kanban read waiting on a sqlite lock


async def _parked_on_executor(scope, receive, send):
    # Loop stays free; the request waits on an executor future, as a slow handler behind a full pool does.
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _slow_handler_body, 1.6)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})


async def _fast(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})


async def _drive(app, path, trace, tick_s=0.05):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        sent.append(m)

    loop = asyncio.get_running_loop()
    stop = threading.Event()

    def ticker():  # the LoopWatch thread's role
        while not stop.is_set():
            trace.tick(loop)
            stop.wait(tick_s)

    th = threading.Thread(target=ticker, daemon=True)
    th.start()
    try:
        await app({"type": "http", "method": "GET", "path": path}, receive, send)
    finally:
        stop.set()
        th.join(2)
    return sent


def test_slow_request_reports_the_await_it_was_parked_on():
    reps = []
    trace = rt.ReqTrace(sink=reps.append, slow_s=1.0, sample_after_s=0.3, sample_s=0.1)
    app = trace.wrap(_parked_on_executor)
    sent = asyncio.run(_drive(app, "/api/plugins/hub-sync/status", trace))
    assert sent[0]["status"] == 200
    assert len(reps) == 1
    rep = reps[0]
    assert rep["kind"] == "slow_request" and rep["path"] == "/api/plugins/hub-sync/status"
    assert 1.4 <= rep["total_s"] <= 3.0 and rep["responded"] and rep["status"] == 200
    assert rep["samples"] >= 3
    joined = " | ".join(" ".join(a["chain"]) for a in rep["awaiting"])
    assert "_parked_on_executor" in joined  # names the handler frame that was awaiting
    # ...and what the executor thread it waits on was running.
    assert any("_slow_handler_body" in " ".join(w["stack"]) for w in rep["worker_threads"])
    json.dumps(rep)


def test_fast_request_reports_nothing():
    reps = []
    trace = rt.ReqTrace(sink=reps.append, slow_s=1.0, sample_after_s=0.3, sample_s=0.1)
    asyncio.run(_drive(trace.wrap(_fast), "/api/x", trace))
    assert reps == [] and trace.inflight == {}


def test_non_http_scope_passes_through():
    seen = []

    async def ws_app(scope, receive, send):
        seen.append(scope["type"])

    trace = rt.ReqTrace(sink=lambda r: None)
    asyncio.run(trace.wrap(ws_app)({"type": "websocket"}, None, None))
    assert seen == ["websocket"] and trace.inflight == {}


def test_noisy_path_cannot_spend_the_hourly_budget_of_other_paths():
    """Hub 2026-11-03 hour 00: 120 slow /api/sessions-heavy reports hit the global cap at 00:32:59.950, so the
    /api/plugins/runners/v1/fleet request that started 00:32:58 and made the Desktop time out at 00:33:03
    left no record at all. The cap is per path now; a path's own flood is still bounded."""
    reps = []
    now = [100.0]
    trace = rt.ReqTrace(sink=reps.append, slow_s=1.0, clock=lambda: now[0])

    def finish(path):
        req = rt._Req(1, "GET", path, now[0] - 3.0, None)
        req.resp_at = now[0]
        trace._finish(req)

    for _ in range(rt.MAX_REPORTS_PER_HOUR + 5):
        finish("/api/sessions")
    finish("/api/plugins/runners/v1/fleet")
    paths = [r["path"] for r in reps]
    assert paths[-1] == "/api/plugins/runners/v1/fleet"
    assert paths.count("/api/sessions") == rt.MAX_REPORTS_PER_PATH_PER_HOUR
    # Query strings do not make a path a new budget.
    for i in range(rt.MAX_REPORTS_PER_PATH_PER_HOUR + 3):
        finish("/api/sessions?limit=%d" % i)
    assert sum(p.startswith("/api/sessions") for p in (r["path"] for r in reps)) == rt.MAX_REPORTS_PER_PATH_PER_HOUR
    # ...and the whole sink stays bounded across many distinct paths.
    for i in range(rt.MAX_REPORTS_PER_HOUR * 2):
        finish("/api/x/%d" % i)
    assert len(reps) == rt.MAX_REPORTS_PER_HOUR


def test_install_on_starlette_app_wraps_once_and_serves():
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    async def slow(request):
        await asyncio.sleep(1.3)
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/slow", slow)])
    reps = []
    trace = rt.ReqTrace(sink=reps.append, slow_s=1.0, sample_after_s=0.3, sample_s=0.1)
    assert rt.install_on_app(app, trace) is True
    assert rt.install_on_app(app, trace) is False  # idempotent
    with TestClient(app) as c:
        assert c.get("/slow").json() == {"ok": True}
    assert len(reps) == 1 and reps[0]["path"] == "/slow" and reps[0]["total_s"] >= 1.2


def test_sink_writes_jsonl_and_log_line(tmp_path, caplog):
    sink = rt.default_sink(tmp_path / "logs" / "loop-watch.jsonl")
    rep = {"kind": "slow_request", "started": "13:26:16.547", "method": "GET", "path": "/api/plugins/hub-sync/status",
           "total_s": 2.79, "responded": True, "status": 200, "samples": 6,
           "awaiting": [{"n": 6, "chain": ["a.py:1 f", "b.py:2 g", "<awaiting Future>"]}], "other_tasks": []}
    with caplog.at_level("WARNING", logger="loop-watch"):
        sink(rep)
    assert "slow request GET /api/plugins/hub-sync/status 2.79s" in caplog.text and "b.py:2 g" in caplog.text
    assert json.loads((tmp_path / "logs" / "loop-watch.jsonl").read_text())["total_s"] == 2.79


def test_sink_names_the_busy_worker_when_the_request_waits_behind_middleware(tmp_path, caplog):
    """The parked chain of a request behind BaseHTTPMiddleware is call_next's stream wait (anyio Event.wait): it
    names no handler, and a card once chased it as a thread-limiter wait. The log line names the busiest worker."""
    sink = rt.default_sink(tmp_path / "loop-watch.jsonl")
    hop = ["starlette/middleware/base.py:193 __call__", "hermes_cli/web_server.py:990 _dashboard_health_middleware",
           "starlette/middleware/base.py:151 call_next", "anyio/streams/memory.py:126 receive",
           "anyio/_backends/_asyncio.py:1866 wait", "3.14/asyncio/locks.py:213 wait", "<awaiting FutureIter>"]
    rep = {"kind": "slow_request", "started": "01:42:34.600", "method": "GET", "path": "/api/sessions/x",
           "total_s": 5.05, "responded": True, "status": 200, "samples": 14,
           "awaiting": [{"n": 14, "chain": hop}], "other_tasks": [],
           "worker_threads": [{"n": 6, "thread": "AnyIO", "stack": [
               "anyio/_backends/_asyncio.py:1033 run", "a.py:1 handler", "ksa.py:1848 attention_task_counts",
               "core.py:629 needs_attention"]}]}
    with caplog.at_level("WARNING", logger="loop-watch"):
        sink(rep)
    assert "busy=AnyIO(6): a.py:1 handler > ksa.py:1848 attention_task_counts > core.py:629 needs_attention" in caplog.text
    caplog.clear()
    rep["awaiting"] = [{"n": 6, "chain": ["a.py:1 f", "b.py:2 g", "<awaiting Future>"]}]
    with caplog.at_level("WARNING", logger="loop-watch"):
        sink(rep)
    assert "busy=" not in caplog.text  # a request parked on its own await already names it


def test_slow_report_carries_query_and_same_path_concurrency():
    """Card t_01a19f5af20a19f6: bursts of ~15 concurrent /api/sessions could not be attributed because the
    report kept only the path. It now carries the query string and how many same-path requests were in flight."""
    reps = []
    trace = rt.ReqTrace(sink=reps.append, slow_s=0.2, sample_after_s=5, sample_s=1)

    async def slowish(scope, receive, send):
        await asyncio.sleep(0.3)
        await send({"type": "http.response.start", "status": 200, "headers": []})

    app = trace.wrap(slowish)

    async def burst():
        async def one(i):
            await app({"type": "http", "method": "GET", "path": "/api/sessions",
                       "query_string": b"limit=100&order=recent&profile=p%d" % i}, None, lambda m: asyncio.sleep(0))
        await asyncio.gather(*(one(i) for i in range(3)))

    asyncio.run(burst())
    slow = [r for r in reps if r["kind"] == "slow_request"]
    assert sorted(r["query"] for r in slow) == ["limit=100&order=recent&profile=p%d" % i for i in range(3)]
    assert sorted(r["inflight_same_path"] for r in slow) == [0, 1, 2]


def test_hourly_request_stats_count_every_request_past_the_report_cap():
    """The per-path report cap (20/h) made loop-watch.jsonl useless as a slow-rate meter: the hourly
    request_stats record counts every request, slow ones included, with percentiles and the slow queries."""
    reps = []
    now = [100.0]
    hour = [1000]
    trace = rt.ReqTrace(sink=reps.append, slow_s=2.0, clock=lambda: now[0])
    orig_time = rt.time.time
    rt.time.time = lambda: hour[0] * 3600 + 5.0
    try:
        def finish(path, dur, query=""):
            req = rt._Req(1, "GET", path, now[0] - dur, None, query, 0)
            req.resp_at = now[0]
            trace._finish(req)

        for i in range(50):
            finish("/api/sessions", 3.0, "limit=100&order=recent")
        for i in range(50):
            finish("/api/sessions", 0.1, "limit=20")
        finish("/api/sessions/20261103_abcdef12/messages", 0.5)
        assert not [r for r in reps if r["kind"] == "request_stats"]
        hour[0] += 1
        finish("/api/status", 0.01)
    finally:
        rt.time.time = orig_time
    slow_reports = [r for r in reps if r["kind"] == "slow_request"]
    assert len(slow_reports) == rt.MAX_REPORTS_PER_PATH_PER_HOUR
    stats = [r for r in reps if r["kind"] == "request_stats"]
    assert len(stats) == 1
    s = stats[0]["paths"]["/api/sessions"]
    assert s["n"] == 100 and s["slow"] == 50
    assert s["p95_s"] == 3.0 and s["p50_s"] in (0.1, 3.0) and s["max_s"] == 3.0
    assert s["slow_queries"] == [["limit=100&order=recent", 50]]
    assert ["limit=20", 50] in s["queries"]
    assert "/api/sessions/:id/messages" in stats[0]["paths"]
    json.dumps(stats[0])


def test_sink_writes_stats_record_without_a_log_line(tmp_path, caplog):
    out = tmp_path / "logs" / "loop-watch.jsonl"
    sink = rt.default_sink(out)
    sink({"kind": "request_stats", "hour": "2026-11-03T13", "paths": {}})
    assert json.loads(out.read_text())["kind"] == "request_stats"
    assert "slow request" not in caplog.text
