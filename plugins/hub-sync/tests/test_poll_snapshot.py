"""Overlapping hub-sync polls return the last snapshot instead of waiting out a slow refresh."""

import asyncio
import importlib.util
import threading
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("hub_sync_poll_snapshot_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_overlapping_poll_returns_last_snapshot_while_refresh_blocks():
    mod = _load()
    setattr(mod, "_POLL_WAIT_S", 30)
    calls = {"n": 0}
    started = threading.Event()
    release = threading.Event()
    overlapped = {}

    def slow():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"n": 1}
        started.set()
        release.wait(10)
        return {"n": 2}

    async def run():
        first = await mod._poll("k", slow)
        assert first == {"n": 1}
        refreshing = asyncio.create_task(mod._poll("k", slow))
        assert await asyncio.to_thread(started.wait, 2)

        async def overlap():
            overlapped["body"] = await mod._poll("k", slow)
            overlapped["done"].set()

        overlapped["done"] = threading.Event()
        asyncio.create_task(overlap())
        try:
            assert await asyncio.to_thread(overlapped["done"].wait, 2)
            assert overlapped["body"] == {"n": 1}
            assert not release.is_set()
        finally:
            release.set()
        assert await refreshing == {"n": 2}

    asyncio.run(run())
