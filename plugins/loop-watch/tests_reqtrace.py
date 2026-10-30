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
