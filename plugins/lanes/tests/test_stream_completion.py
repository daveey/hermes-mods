"""A completed projection is ready work, not a reason to wait a watch tick."""
import asyncio
import time
from types import SimpleNamespace

from test_view import load


def test_completed_projection_wakes_sender_without_another_cursor_poll(monkeypatch):
    api = load()
    monkeypatch.setattr(api, '_projector_enabled', False)
    monkeypatch.setattr(api, '_identity', SimpleNamespace(matches=lambda _: True))
    monkeypatch.setattr(api, 'change_cursor', lambda: ['fixed'])
    monkeypatch.setattr(api, '_refresh_cursor', lambda _: None)
    monkeypatch.setattr(api, 'WATCH_S', 1.)
    monkeypatch.setattr(api, 'HEARTBEAT_S', 30.)
    monkeypatch.setattr(api, 'lane_view', lambda *args: {'configured': False})

    class Socket:
        def __init__(self):
            self.sent = asyncio.Event()
            self.frames = []

        async def send_json(self, frame):
            self.frames.append(frame)
            self.sent.set()

        async def receive(self):
            await self.sent.wait()
            return {'type': 'websocket.disconnect'}

    async def probe():
        socket = Socket()
        start = time.monotonic()
        # Zero-cost projection must not wait the full 1 s watch period.
        await asyncio.wait_for(api._stream_engine.run(api, socket, (6., '6h', False), True), .6)
        assert time.monotonic() - start < .6
        assert len(socket.frames) == 1
        assert socket.frames[0]['view'] == {'configured': False}

    asyncio.run(probe())
