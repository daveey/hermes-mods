"""hubview reads run in a hubview_worker.py child, not on the gateway's threads (plugin_api._Reader).

2026-10-30 11:08-11:27 PDT: hubview's pure-Python decode on hub-sync's 4 in-gateway threads held the GIL against the
gateway's asyncio loop (loop thread 64% of samples in take_gil), and 666 Desktop reads on conn=local timed out.
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

from conftest import HERE, PLUGIN, _load

pytest.importorskip("fastapi")

WORKER = PLUGIN / "hubview_worker.py"
VIEW = PLUGIN / "hubview.py"


def _api(name):
    spec = importlib.util.spec_from_file_location(name, PLUGIN / "dashboard" / "plugin_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch, fake_client):
    from conftest import build_db
    state = tmp_path / "hubstate"
    state.mkdir()
    build_db(state / "hub.db", fake_client.SCHEMA)
    monkeypatch.setenv("HERMES_HUB_STATE", str(state))
    monkeypatch.setenv("HERMES_HUB_CLIENT", str(HERE / "fake_hermes_hub.py"))
    return state


@pytest.fixture
def api(env):
    mod = _api("hub_sync_api_child_test")
    assert mod._READER is not None, "the gateway must read through the child by default"
    yield mod
    mod._READER.close()


def _child_pids(reader):
    return [c.proc.pid for c in reader.children if c.proc is not None]


def test_reads_run_in_a_child_process_and_match_the_in_process_view(api, env):
    hv = _load(VIEW, "hubview_child_compare")
    for fn, args in [("status", []), ("sessions", [None, [], False, 500]), ("inbox", [None, [], False]),
                     ("tasks", [None, [], None, True]), ("where", ["s_mini"]),
                     ("session_links", [["s_local"], False, None, None]), ("requests", [50])]:
        body = json.loads(api._READER.call(fn, args))
        local = json.loads(json.dumps(getattr(hv, fn)(*args), default=str))
        # own rows' synced_at/seen_at are "now" in each process; everything else must be identical
        drop = lambda o: {k: drop(v) for k, v in o.items() if k not in ("synced_at", "seen_at")} if isinstance(o, dict) \
            else [drop(x) for x in o] if isinstance(o, list) else o
        assert drop(body) == drop(local), fn
        assert body["available"] is True
    pids = _child_pids(api._READER)
    assert pids and os.getpid() not in pids
    for pid in pids:
        os.kill(pid, 0)  # alive


def test_endpoint_relays_child_bytes_without_reencoding(api, env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    calls = []
    real = api._READER.call
    monkeypatch.setattr(api._READER, "call", lambda fn, args: calls.append(fn) or real(fn, args))
    monkeypatch.setattr(api, "_hv", lambda: pytest.fail("in-process read used while the child is healthy"))
    app = FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    r = c.get("/status")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert r.json()["gateway"] == "laptop" and {g["gateway"] for g in r.json()["gateways"]} >= {"mini", "cloud"}
    r = c.get("/session-links/s_local")
    assert {t["id"] for t in r.json()["tasks"]} == {"t_own", "t_mini"}
    assert calls == ["status", "session_links"]


def test_an_idle_child_that_died_is_replaced_transparently(api, env):
    r = api._READER
    json.loads(r.call("status", []))
    old = _child_pids(r)
    for c in r.children:
        if c.proc is not None:
            c.proc.kill()
            c.proc.wait()
    assert json.loads(r.call("status", []))["available"] is True
    assert r.failures == 0 and not set(_child_pids(r)) & set(old)


def test_a_child_dying_mid_read_falls_back_in_process_then_respawns_after_backoff(tmp_path, env, monkeypatch):
    api = _api("hub_sync_api_child_dies")
    api._READER.close()
    view = tmp_path / "dyingview.py"
    view.write_text("import os\ndef status():\n    os._exit(3)\n")
    r = api._Reader(view, WORKER, children=1, timeout_s=5, backoff_s=0.3)
    monkeypatch.setattr(api, "_READER", r)
    monkeypatch.setattr(api, "_hv", lambda: type("HV", (), {"status": staticmethod(lambda: {"inproc": True})}))
    assert json.loads(api._read("status")) == {"inproc": True}
    assert r.failures == 1 and r.children[0].proc is None
    with pytest.raises(api.ChildUnavailable, match="backing off"):
        r.call("status", [])
    view.write_text("def status():\n    return {'child': True}\n")
    os.utime(view, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
    time.sleep(0.35)
    assert json.loads(api._read("status")) == {"child": True}
    r.close()


def test_a_hung_child_times_out_and_is_killed(tmp_path, env):
    api = _api("hub_sync_api_child_hang")
    api._READER.close()
    slow = tmp_path / "slowview.py"
    slow.write_text("import time\ndef status():\n    time.sleep(30)\n    return {}\n")
    r = api._Reader(slow, WORKER, children=1, timeout_s=0.5, backoff_s=60)
    t = time.monotonic()
    with pytest.raises(api.ChildUnavailable):
        r.call("status", [])
    assert time.monotonic() - t < 3
    assert r.children[0].proc is None  # killed, not left sleeping
    assert r.down_until > time.monotonic()


def test_a_hubview_error_in_a_healthy_child_does_not_disable_children(tmp_path, env):
    api = _api("hub_sync_api_child_error")
    api._READER.close()
    bad = tmp_path / "badview.py"
    bad.write_text("def status():\n    raise RuntimeError('boom')\ndef where(s):\n    return {'s': s}\n")
    r = api._Reader(bad, WORKER, children=1, timeout_s=5, backoff_s=60)
    with pytest.raises(api.ChildReadError, match="boom"):
        r.call("status", [])
    assert r.down_until == 0.0
    assert json.loads(r.call("where", ["x"])) == {"s": "x"}
    r.close()


def test_a_changed_hubview_replaces_the_child(tmp_path, env):
    api = _api("hub_sync_api_child_reload")
    api._READER.close()
    view = tmp_path / "view.py"
    view.write_text("def status():\n    return {'v': 1}\n")
    r = api._Reader(view, WORKER, children=1, timeout_s=5)
    assert json.loads(r.call("status", [])) == {"v": 1}
    pid = r.children[0].proc.pid
    view.write_text("def status():\n    return {'v': 2}\n")
    os.utime(view, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
    assert json.loads(r.call("status", [])) == {"v": 2}
    assert r.children[0].proc.pid != pid
    r.close()


def test_worker_refuses_anything_but_reads(tmp_path):
    import io
    spec = importlib.util.spec_from_file_location("hub_worker_t", WORKER)
    w = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w)

    class V:
        def request(self, *a):
            raise AssertionError("write reached")
    out = io.BytesIO()
    w.serve(V(), io.BytesIO(b'{"fn": "request", "args": [{}]}\n'), out)
    head = json.loads(out.getvalue().split(b"\n", 1)[0])
    assert head["ok"] is False and "not a hubview read" in head["error"]


def test_inproc_env_disables_the_child(monkeypatch, env):
    monkeypatch.setenv("HUB_SYNC_INPROC", "1")
    assert _api("hub_sync_api_inproc")._READER is None
