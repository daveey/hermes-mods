"""Projection must cross the real child boundary and partition poll caches."""
import asyncio
import json
import os

import pytest

from conftest import PLUGIN, _load
from test_read_child import api, env, _child_pids  # reuse real subprocess fixtures
from test_sessions_projection import projected


def test_real_child_endpoint_and_spawn_failure_fallback(api, env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(api.router)
    # Replica-only results avoid intentionally time-varying local synced_at.
    query = "/sessions?exclude=laptop&archived=1"
    with TestClient(app) as client:
        full = client.get(query)
        real_hv = api._hv
        monkeypatch.setattr(api, "_hv", lambda: pytest.fail("healthy child unexpectedly fell back"))
        small = client.get(query + "&projection=prefetch")
        assert small.json() == projected(full.json())
        assert _child_pids(api._READER) and os.getpid() not in _child_pids(api._READER)
        monkeypatch.setattr(api, "_hv", real_hv)
        api._READER.close()
        # A nonexistent interpreter exercises the actual spawn failure -> _read fallback.
        broken = api._Reader(PLUGIN / "hubview.py", PLUGIN / "hubview_worker.py",
                             python=str(env / "no-python"))
        monkeypatch.setattr(api, "_READER", broken)
        api._snapshots.clear()
        try:
            fallback = client.get(query + "&projection=prefetch")
            assert fallback.content == small.content
            assert broken.failures == 1 and not _child_pids(broken)
            assert client.get(query).content == full.content
        finally:
            broken.close()


@pytest.mark.parametrize("first", [None, "prefetch"])
def test_concurrent_cold_warming_and_stale_snapshots_are_partitioned(hub, monkeypatch, first):
    import httpx
    from fastapi import FastAPI
    hv, _ = hub
    api = _load(PLUGIN / "dashboard/plugin_api.py", "projection_poll_test")
    api._READER.close()
    monkeypatch.setattr(api, "_READER", None)
    monkeypatch.setattr(api, "_hv", lambda: hv)
    monkeypatch.setattr(api, "_COLD_WAIT_S", 0.01)
    monkeypatch.setattr(api, "_POLL_WAIT_S", 0.01)
    app = FastAPI()
    app.include_router(api.router)

    async def run():
        gates = {None: asyncio.Event(), "prefetch": asyncio.Event()}
        entered = []
        original = api._run_timed

        async def blocked(key, fn):
            entered.append(key)
            await gates[key[-1]].wait()
            return await original(key, fn)

        monkeypatch.setattr(api, "_run_timed", blocked)
        order = [first, "prefetch" if first is None else None]
        urls = {p: "/sessions" + ("?projection=prefetch" if p else "") for p in order}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            cold = await asyncio.gather(*(client.get(urls[p]) for p in order))
            assert all(r.json() == {"available": False, "warming": True} for r in cold)
            assert len(api._flights) == 2 and len(entered) == 2
            assert {key[-1] for key in api._flights} == {None, "prefetch"}
            # Finish one flavor; the other remains cold, not a snapshot of its sibling.
            done = api._flights[("sessions", None, (), False, 500, first)]
            gates[first].set()
            await done
            other = order[1]
            assert (await client.get(urls[other])).json() == {"available": False, "warming": True}
            gates[other].set()
            await asyncio.gather(*list(api._flights.values()))
            assert len(api._snapshots) == 2
            expected = {p: json.loads(api._snapshots[("sessions", None, (), False, 500, p)]) for p in order}
            assert expected["prefetch"] == projected(expected[None])
            assert "title" in expected[None]["sessions"][0]
            # Both refreshes now block. First requests time out to their own snapshot;
            # overlapping requests use the same flights and the same flavor snapshots.
            for gate in gates.values():
                gate.clear()
            entered.clear()
            for _ in range(2):
                responses = await asyncio.gather(*(client.get(urls[p]) for p in order))
                assert [r.json() for r in responses] == [expected[p] for p in order]
                assert len(api._flights) == 2 and len(entered) == 2
            for gate in gates.values():
                gate.set()
            await asyncio.gather(*list(api._flights.values()))
    asyncio.run(run())
