"""A cold hub-sync poll (no snapshot yet) answers "warming" instead of outliving the Desktop's 4 s timeout.

Evidence (MacBook, 2026-10-28 07:03 PDT, backend pid 50733 booted 07:03:45): the Desktop's first
GET hub-sync/sessions?limit=300 and /where/<id> timed out at 4 s. agent.log shows the handlers entered
at 07:03:46.67 / 46.88 with queue=0.00 s and run=6.5 s: three concurrent cold reads building hubview's
decode cache under boot CPU contention (MCP discovery, gateway start). The same cold build takes
~0.75 s in an idle process. A cold key had no snapshot to fall back on, so the request waited the
whole read out.
"""

import asyncio
import importlib.util
import threading
import time
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("hub_sync_poll_cold_warming_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cold_poll_answers_warming_within_bound_then_serves_the_real_read():
    mod = _load()
    setattr(mod, "_COLD_WAIT_S", 0.2)
    release = threading.Event()

    def slow_cold_read():
        release.wait(10)
        return {"available": True, "sessions": [1, 2]}

    async def run():
        t = time.perf_counter()
        first = await mod._poll(("sessions", 300), slow_cold_read)
        elapsed = time.perf_counter() - t
        # Answered quickly with an explicit "not ready" body the Desktop treats as no answer.
        assert elapsed < 1.0, elapsed
        assert first.get("available") is False and first.get("warming") is True, first
        # The read keeps running in the background and fills the snapshot.
        release.set()
        for _ in range(100):
            if mod._snapshots.get(("sessions", 300)) is not None:
                break
            await asyncio.sleep(0.01)
        second = await mod._poll(("sessions", 300), slow_cold_read)
        assert second == {"available": True, "sessions": [1, 2]}

    asyncio.run(run())


def test_fast_cold_poll_still_returns_the_real_answer():
    mod = _load()

    async def run():
        return await mod._poll("status", lambda: {"available": True, "gateway": "laptop"})

    assert asyncio.run(run()) == {"available": True, "gateway": "laptop"}
