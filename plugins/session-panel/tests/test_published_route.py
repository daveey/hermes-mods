"""The real HTTP entry serves pre-serialized publication bytes only."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse
from test_routes import load
from test_feed import Readers


def test_http_read_matches_legacy_bytes_without_lane_json_or_compute(monkeypatch):
    api = load('_spf_published_route', Path(__file__).resolve().parents[1] / 'dashboard/plugin_api.py')
    core = api._load_feed()
    f = core.Feed(Readers(), emit=lambda p: None)
    monkeypatch.setattr(core, 'PROCESS_FEED', f)
    out = f.changes(['S1'])
    # The wire is the in-process view minus the hub-only inputs (feed.WIRE_OMIT), byte for byte otherwise.
    out = {**out, 'bundles': {s: f._client_bundle(s, b, f._bundle_fresh(s, f.clock())) for s, b in out['bundles'].items()}}
    expected = JSONResponse(api.json_safe({'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE, **out})).body
    def forbidden(*a, **kw):
        raise AssertionError('interactive read invoked compute, serialization or a lane')
    monkeypatch.setattr(api, '_on_lane', forbidden)
    monkeypatch.setattr(api, 'json_safe', forbidden)
    monkeypatch.setattr(api, 'feed', forbidden)
    monkeypatch.setattr(f, 'compute', forbidden)
    app = FastAPI()
    app.include_router(api.router)
    response = TestClient(app).post('/changes', json={'sessions': ['S1']})
    assert response.status_code == 200
    assert response.content == expected
    cold = TestClient(app).post('/changes', json={'sessions': ['S2']})
    assert cold.json()['bundles'] == {}


def test_negotiated_gzip_preserves_exact_published_json_and_legacy_identity(monkeypatch):
    import asyncio
    import gzip
    import threading
    import types
    api = load('_spf_gzip_route', Path(__file__).resolve().parents[1] / 'dashboard/plugin_api.py')
    core = api._load_feed()
    wire = b'{"epoch":"E","rev":9,"bundles":{"S":{"model":{"history":"' + b'unchanged-row;' * 8000 + b'"}}}}'
    monkeypatch.setattr(core, 'PROCESS_FEED', types.SimpleNamespace(changes_bytes=lambda *a, **kw: wire))
    monkeypatch.setattr(api, '_external_projection', lambda: None)
    # New clients explicitly negotiate in their body; old clients retain identity.
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        identity = client.post('/changes', json={'sessions': ['S']})
        compressed = client.post('/changes', json={'sessions': ['S'], 'response_encoding': 'gzip'})
    assert identity.status_code == compressed.status_code == 200
    assert compressed.headers.get('content-encoding') == 'gzip'
    assert compressed.content == identity.content
    assert int(compressed.headers['content-length']) < len(identity.content) // 10
    assert 'content-encoding' not in identity.headers
    # Exercise raw body, not just TestClient's transparent decompression.
    seen = []
    async def lane(name, fn, *args, **kwargs):
        seen.append((name, threading.get_ident(), kwargs['deadline']))
        return await asyncio.to_thread(fn, *args)
    monkeypatch.setattr(api, '_on_lane', lane)
    async def check():
        response = await api.changes(api.ChangesBody(sessions=['S'], response_encoding='gzip'), None)
        assert gzip.decompress(response.body) == identity.content
    asyncio.run(check())
    assert seen and seen[0][0] == 'feed' and seen[0][2] <= .05


def test_gzip_bounds_busy_lane_and_incompressible_body_keep_identity(monkeypatch):
    import asyncio
    import os
    from fastapi import HTTPException
    api = load('_spf_gzip_bounds', Path(__file__).resolve().parents[1] / 'dashboard/plugin_api.py')
    async def busy(*a, **kw):
        raise HTTPException(503, 'lane busy')
    monkeypatch.setattr(api, '_on_lane', busy)
    body = b'a' * 50000
    async def check():
        response = await api._changes_response(body, 'gzip', None)
        assert response.body == body and 'content-encoding' not in response.headers
    asyncio.run(check())
    assert api._gzip_changes(b'small') is None
    assert api._gzip_changes(b'x' * (16 * 1024 * 1024 + 1)) is None
    assert api._gzip_changes(os.urandom(50000)) is None


def test_external_projection_encoding_is_not_a_producer_identity(monkeypatch):
    import sys
    import types
    from starlette.responses import Response
    api = load('_spf_gzip_external', Path(__file__).resolve().parents[1] / 'dashboard/plugin_api.py')
    seen = []
    wire = b'{"published":"' + b'x' * 50000 + b'"}'
    def projection(relay, body):
        seen.append(body)
        return Response(wire, media_type='application/json')
    monkeypatch.setitem(sys.modules, 'hermes_cli.serve_producer_runtime', types.SimpleNamespace(projection_response=projection))
    monkeypatch.setattr(api, '_external_projection', lambda: object())
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        response = client.post('/changes', json={'sessions': ['S'], 'response_encoding': 'gzip'})
    assert response.headers.get('content-encoding') == 'gzip'
    assert response.content == wire
    assert 'response_encoding' not in seen[0]


def test_gzip_exact_boundary_unknown_encoding_and_errors(monkeypatch):
    import asyncio
    import gzip
    import sys
    import types
    from starlette.responses import Response
    api = load('_spf_gzip_errors', Path(__file__).resolve().parents[1] / 'dashboard/plugin_api.py')
    maximum = b'x' * (16 * 1024 * 1024)
    assert gzip.decompress(api._gzip_changes(maximum)) == maximum
    async def forbidden(*a, **kw):
        raise AssertionError('identity must not wait on compression')
    monkeypatch.setattr(api, '_on_lane', forbidden)
    async def check():
        for encoding in (None, 'br', 'GZIP', 'identity'):
            response = await api._changes_response(b'x' * 50000, encoding, None)
            assert response.body == b'x' * 50000 and 'content-encoding' not in response.headers
    asyncio.run(check())
    original = Response(b'{"detail":"unavailable"}', status_code=503, headers={'Retry-After': '1'})
    monkeypatch.setitem(sys.modules, 'hermes_cli.serve_producer_runtime', types.SimpleNamespace(projection_response=lambda *a: original))
    monkeypatch.setattr(api, '_external_projection', lambda: object())
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        response = client.post('/changes', json={'sessions': ['S'], 'response_encoding': 'gzip'})
    assert response.status_code == 503 and response.content == original.body
    assert response.headers['retry-after'] == '1' and 'content-encoding' not in response.headers
