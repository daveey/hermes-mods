"""Real ASGI dispatch with inert core boundaries; never start an updater or Desktop."""
from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
from contextvars import ContextVar
import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest


@pytest.fixture
def api():
    spec = importlib.util.spec_from_file_location(
        "core_update_api_test", Path(__file__).resolve().parents[1] / "dashboard/plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("method,path", [("GET", "/status"), ("POST", "/restart")])
def test_slow_core_keeps_ping_responsive(api, monkeypatch, method, path):
    entered, release = threading.Event(), threading.Event()
    profile = ContextVar("fixture_profile", default="unset")
    calls, observations, errors = [], [], []
    payload = {"state": "ready", "can_restart": True} if path == "/status" else {"ok": True, "sha": "a" * 40}

    def slow_core():
        calls.append((profile.get(), threading.get_ident()))
        entered.set()
        assert release.wait(10), "fixture watchdog: core was not released"
        return payload

    monkeypatch.setattr(api, "_core", lambda: SimpleNamespace(status=slow_core, restart=slow_core))
    app = FastAPI()
    app.include_router(api.router)

    @app.middleware("http")
    async def profile_context(request, call_next):
        token = profile.set("fixture-profile")
        try:
            return await call_next(request)
        finally:
            profile.reset(token)

    async def exercise():
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            slow = asyncio.create_task(client.request(method, path))

            def observe():
                ping = None
                try:
                    assert entered.wait(5), "core boundary never entered"
                    ping = asyncio.run_coroutine_threadsafe(client.get("/ping"), loop)
                    response = ping.result(timeout=1)
                    observations.append((response.status_code, response.json(), not slow.done(), not release.is_set()))
                except FutureTimeout:
                    errors.append("ping blocked behind synchronous core")
                except BaseException as exc:
                    errors.append(repr(exc))
                finally:
                    release.set()  # also unblocks the original async route on RED
                    if ping is not None:
                        ping.cancel()

            observer = threading.Thread(target=observe)
            observer.start()
            try:
                response = await slow
                assert response.status_code == 200 and response.json() == payload
            finally:
                release.set()
                await asyncio.to_thread(observer.join, 5)
            assert not observer.is_alive()
            assert not errors, errors
            assert observations == [(200, {"ok": True, "api": 2}, True, True)]
            assert len(calls) == 1  # restart must not be retried or duplicated
            assert calls[0][0] == "fixture-profile"
            assert calls[0][1] != loop_thread

    asyncio.run(exercise())


def test_concurrent_core_load_publishes_once(api, monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import sys

    source = tmp_path / "core.py"
    source.write_text("ready = True\n")
    key = f"_core_update_core_{source.stat().st_mtime_ns}"
    monkeypatch.delitem(sys.modules, key, raising=False)
    monkeypatch.setattr(api, "_CORE", source)
    entered, release, duplicate = threading.Event(), threading.Event(), threading.Event()
    calls = []
    real_spec = api.importlib.util.spec_from_file_location

    def spec_with_pause(*args):
        spec = real_spec(*args)
        execute = spec.loader.exec_module

        def paused(module):
            calls.append(module)
            if len(calls) > 1:
                duplicate.set()
            entered.set()
            assert release.wait(5), "fixture loader watchdog"
            execute(module)

        spec.loader.exec_module = paused
        return spec

    monkeypatch.setattr(api.importlib.util, "spec_from_file_location", spec_with_pause)
    start = threading.Barrier(9)

    def load():
        start.wait(timeout=5)
        return api._core()

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(load) for _ in range(8)]
            try:
                start.wait(timeout=5)
                assert entered.wait(5)
                assert key not in sys.modules  # no half-initialized publication
                duplicate.wait(1)  # allow competing cold callers to reach the held loader
            finally:
                release.set()
            modules = [future.result(timeout=5) for future in futures]
        assert len(calls) == 1
        assert all(module is modules[0] and module.ready for module in modules)
        assert api._core() is modules[0]
    finally:
        sys.modules.pop(key, None)


@pytest.mark.parametrize("path", ["/status", "/restart"])
def test_error_contract(api, monkeypatch, path):
    def fail():
        raise RuntimeError("x" * 250)

    monkeypatch.setattr(api, "_core", fail)
    app = FastAPI()
    app.include_router(api.router)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            if path == "/status":
                response = await client.get(path)
                assert response.status_code == 200
                assert response.json() == {"state": "unavailable", "error": "x" * 200}
            else:
                with pytest.raises(RuntimeError, match="^x{250}$"):
                    await client.post(path)

    asyncio.run(exercise())
