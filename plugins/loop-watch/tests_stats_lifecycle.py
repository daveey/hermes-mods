"""Real server lifecycle, including installing after lifespan has started."""
import json
import subprocess
import sys
from pathlib import Path

from tests_reqtrace import rt
import pytest


@pytest.mark.parametrize('wrapped_app', [False, True])
def test_late_installed_uvicorn_sigterm_flushes_tail(tmp_path, wrapped_app):
    pytest.importorskip('uvicorn')  # optional host; production and local gate exercise it
    code = '''
import asyncio, importlib.util, os, signal, sys, threading
from pathlib import Path
from starlette.applications import Starlette
import uvicorn
s = importlib.util.spec_from_file_location('rt', sys.argv[1])
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
app = Starlette()
def wrap(app):
    async def wrapped(scope, receive, send):
        return await app(scope, receive, send)
    return wrapped
served = wrap(app) if sys.argv[3] == 'True' else app
server = uvicorn.Server(uvicorn.Config(served, host='127.0.0.1', port=0, log_level='error'))
async def install_late():
    while not server.started:
        await asyncio.sleep(.01)
    t = m.ReqTrace(sink=m.default_sink(Path(sys.argv[2])))
    assert m.install_on_app(app, t)
    t.start_stats(interval_s=60)
    t._record_stats(m._Req(1, 'GET', '/probe', 0, None), 3, 100)
    os.kill(os.getpid(), signal.SIGTERM)
async def run():
    asyncio.create_task(install_late())
    await server.serve()
asyncio.run(run())
'''
    out = tmp_path / 'stats.jsonl'
    result = subprocess.run([sys.executable, '-c', code, str(rt.__file__), str(out), str(wrapped_app)], timeout=15)
    assert result.returncode in (0, -15)
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert sum(r['paths']['/probe']['n'] for r in rows if r['kind'] == 'request_stats') == 1
    assert any(r.get('reason') == 'shutdown' for r in rows)


def test_server_shutdown_preserves_other_servers_arguments_and_errors(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    uvicorn = pytest.importorskip('uvicorn')
    calls, closed = [], []
    async def original(self, *args, **kwargs):
        calls.append((args, kwargs))
        if kwargs.get('fail'):
            raise ValueError('original shutdown failed')
        return 42
    monkeypatch.setattr(uvicorn.Server, 'shutdown', original)
    app = SimpleNamespace()
    trace = SimpleNamespace(close_stats=lambda: closed.append(True))
    rt._install_server_shutdown(app, trace)
    wrapped = uvicorn.Server.shutdown
    rt._install_server_shutdown(app, trace)
    assert uvicorn.Server.shutdown is wrapped
    unrelated = SimpleNamespace(config=SimpleNamespace(app=SimpleNamespace()))
    assert asyncio.run(wrapped(unrelated, 'arg', sockets=[])) == 42
    assert closed == []
    owner = SimpleNamespace(config=SimpleNamespace(app=app))
    with pytest.raises(ValueError, match='original shutdown failed'):
        asyncio.run(wrapped(owner, fail=True))
    assert closed == [True]
    assert calls == [(('arg',), {'sockets': []}), ((), {'fail': True})]
    gateway = SimpleNamespace(config=SimpleNamespace(app=SimpleNamespace(web=SimpleNamespace(app=app))))
    assert asyncio.run(wrapped(gateway)) == 42
    assert closed == [True, True]
    for inner in (SimpleNamespace(app=app), SimpleNamespace(__wrapped__=app)):
        assert rt._server_trace(inner) is trace
    cycle = SimpleNamespace()
    cycle.app = cycle
    assert rt._server_trace(cycle) is None
