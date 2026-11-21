"""Push: `plugin.inbox.changed` {feed, seq} when an inbox sidecar is written (post, update, act, archive), from this
API or from any other process (the watcher compares the sidecar stat), and never for a read."""
import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.needs_core  # the real core store writes the sidecar

HERE = Path(__file__).resolve().parents[1]


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    pytest.importorskip("fastapi")
    spec = importlib.util.spec_from_file_location("_inbox_push_test", HERE / "dashboard" / "plugin_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_profile", lambda: "default")
    monkeypatch.setattr(mod, "WATCH_TICK_S", 3600)  # the tests drive check() themselves
    sent = []
    monkeypatch.setattr(mod, "_emit", sent.append)
    sys.path.insert(0, str(Path(__file__).parent))
    from ask_fixtures import register_test_posters
    register_test_posters(tmp_path)
    yield mod, sent, tmp_path
    mod.WATCH.stop.set()


def test_store_writes_push_and_reads_do_not(api):
    mod, sent, root = api
    from ask_fixtures import ASK
    st = mod._store()
    w = mod._Watch()
    assert w.check() is False  # baseline
    a = st.Inbox().post("Pick one", "b", "do", poster="t", ask=ASK)
    assert w.check() is True
    assert sent == [{"feed": mod.FEED_ID, "seq": 1}]
    st.Inbox().list()
    st.Inbox().get(a["session_id"])
    assert w.check() is False, "a read must not look like a write (else every refresh would push again)"
    st.Inbox().post("Pick one", "b2", "do", key=None, poster="t", ask=ASK)
    assert w.check() is True
    st.Inbox().archive(a["session_id"])
    assert w.check() is True
    assert [p["seq"] for p in sent] == [1, 2, 3]
    assert w.check() is False


def test_root_dashboard_watches_profile_sidecars(api):
    mod, sent, root = api
    from ask_fixtures import ASK, register_test_posters
    st = mod._store()
    prof = root / "profiles" / "alpha"
    prof.mkdir(parents=True)
    register_test_posters(prof)
    st.Inbox.for_home(prof).post("seed", "b", "do", poster="t", ask=ASK)  # the profile now has an inbox
    w = mod._Watch()
    w.check()
    st.Inbox.for_home(prof).post("Profile card", "b", "do", poster="t", ask=ASK)
    assert w.check() is True and sent[-1]["feed"] == mod.FEED_ID


def test_rest_items_carries_feed_and_writes_push_at_once(api):
    mod, sent, root = api
    import fastapi
    from fastapi.testclient import TestClient
    from ask_fixtures import ASK
    app = fastapi.FastAPI()
    app.include_router(mod.router)
    c = TestClient(app)
    r = c.get("/items").json()
    assert r["feed"] == mod.FEED_ID and r["items"] == []
    assert mod.WATCH.thread is not None and mod.WATCH.thread.is_alive()
    assert sent == []
    sid = c.post("/items", json={"title": "t", "body": "b", "accept_prompt": "p", "poster": "t", "ask": ASK}).json()["session_id"]
    assert len(sent) == 1, "a POST pushes without waiting for the watcher tick"
    c.get("/items")
    assert len(sent) == 1
    assert c.post(f"/items/{sid}/archive").json() == {"ok": True}
    assert [p["seq"] for p in sent] == [1, 2]
