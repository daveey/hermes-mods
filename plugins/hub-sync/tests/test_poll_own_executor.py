"""hub-sync polls must not queue behind the event loop's shared default executor.

At backend boot the Desktop's first GET hub-sync/sessions and /where/<id> timed out at 4 s on two
restarts in a row (desktop.log 06:13:25 and 06:38:05 PDT 2026-10-28) although the reads take under
0.1 s cold: other boot work held asyncio's default executor (min(32, cpu+4) threads, shared with
~370 core call sites). The fix gives hub-sync its own small pool.
"""

import asyncio
import concurrent.futures
import importlib.util
import threading
import time
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("hub_sync_poll_own_executor_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cold_poll_returns_while_default_executor_is_saturated():
    mod = _load()
    release = threading.Event()

    async def run():
        loop = asyncio.get_running_loop()
        shared = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        loop.set_default_executor(shared)
        # Occupy every default-executor thread, as boot work does.
        hogs = [loop.run_in_executor(None, release.wait, 10) for _ in range(2)]
        await asyncio.sleep(0.05)
        try:
            t = time.perf_counter()
            body = await asyncio.wait_for(mod._poll("cold", lambda: {"ok": True}), timeout=3)
            elapsed = time.perf_counter() - t
        finally:
            release.set()
            await asyncio.gather(*hogs)
        assert body == {"ok": True}
        assert elapsed < 1.0, elapsed

    asyncio.run(run())


def test_slow_poll_is_logged_with_queue_and_run_split(caplog):
    mod = _load()
    setattr(mod, "_SLOW_LOG_S", 0.05)

    def slow():
        time.sleep(0.1)
        return {"ok": True}

    async def run():
        return await mod._poll(("where", "x"), slow)

    with caplog.at_level("WARNING"):
        assert asyncio.run(run()) == {"ok": True}
    lines = [r.getMessage() for r in caplog.records if "hub-sync slow poll" in r.getMessage()]
    assert lines, [r.getMessage() for r in caplog.records]
    assert "queue=" in lines[0] and "run=" in lines[0] and "default_backlog=" in lines[0]
