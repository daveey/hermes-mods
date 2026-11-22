"""Actual mounted routes retain wire parity without request-time source access."""
import builtins
import io
import os
import socket
import sqlite3
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_fact_lifecycle import api
from test_feed import feedmod
from test_fact_subscription import Transport, Key, bridge


def test_mounted_facts_routes_no_io_and_exact_wire(api, monkeypatch):
    f = api._load_feed().PROCESS_FEED
    f.clock = lambda: 1000.0
    bundle = f.compute('S1')
    transport = Transport()
    adapter = bridge.PanelSubscription(f, transport, 'profile-a', Key)
    f._fact_subscription = adapter
    f.changes_bytes(['S1'])
    adapter.step()
    transport.send(bundle)
    adapter.step()
    expected = {'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE,
                'bundle': f.decision_projection(bundle), 'epoch': f.epoch, 'rev': f.rev}
    expected_status = {'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE,
                       'running': True, **f.status()}
    app = FastAPI()
    app.include_router(api.router)
    armed = False
    calls = []

    def forbidden(*args, **kwargs):
        import sys
        frame = sys._getframe(1)
        chain = []
        while frame is not None:
            chain.append((frame.f_code.co_filename, frame.f_code.co_name, frame.f_lineno))
            frame = frame.f_back
        calls.append(chain)
        raise AssertionError('request performed source I/O or re-encoded a view')

    @app.middleware('http')
    async def guard(request, call_next):
        if not armed:
            return await call_next(request)
        with monkeypatch.context() as m:
            for obj, names in ((builtins, ('open',)), (io, ('open',)), (sqlite3, ('connect',)),
                               (socket, ('socket', 'create_connection')),
                               (subprocess, ('Popen', 'run')),
                               (os, ('stat', 'open', 'listdir', 'scandir'))):
                for name in names:
                    m.setattr(obj, name, forbidden)
            m.setattr(api, 'CoreReaders', forbidden)
            m.setattr(api, '_load_feed', forbidden)
            m.setattr(f, 'compute', forbidden)
            m.setattr(f, '_encode', forbidden)
            return await call_next(request)

    try:
        with TestClient(app) as client:
            client.get('/status')  # initialize framework and sync worker pool
            client.post('/changes', json={'sessions': ['S1']})
            client.post('/decision-refresh', json={'session': 'S1'})
            armed = True
            result = client.post('/changes', json={'sessions': ['S1']})
            assert result.status_code == 200
            assert result.json()['bundles']['S1'] == feedmod.wire_bundle(bundle)
            assert client.get('/status').json() == expected_status
            assert client.post('/decision-refresh', json={'session': 'S1'}).json() == expected
            assert client.post('/decision-refresh', json={'session': 'cold'}).status_code == 503
            assert client.post('/changes', json={'sessions': ['cold']}).json()['bundles'] == {}
            assert calls == [], repr(calls)
            armed = False
            adapter.step()  # admit the cold watch and replace the subscription
            transport.send(bundle, health='stale', rev=2)
            adapter.step()
            armed = True
            assert client.post('/decision-refresh', json={'session': 'S1'}).status_code == 503
            assert client.post('/changes', json={'sessions': ['S1']}).json()['bundles']
            assert calls == [], repr(calls)
    finally:
        adapter.close()
