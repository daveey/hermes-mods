"""REST tests: the root dashboard lists and acts on messages posted by named profiles.

Run: PYTHONPATH=<hermes-agent> uv run --python 3.11 --with pytest --with ruamel.yaml --with pyyaml \
     --with python-dotenv --with fastapi --with httpx python -m pytest -q -p no:cacheprovider plugins/inbox/tests
"""
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    spec = importlib.util.spec_from_file_location("_inbox_api_test", HERE / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    monkeypatch.setattr(api, "_profile", lambda: "default")
    app = fastapi.FastAPI()
    app.include_router(api.router)
    return TestClient(app), api, tmp_path


def test_root_lists_profile_items_and_accepts_them_by_profile(client):
    c, api, root = client
    st = api._store()
    prof = root / "profiles" / "alpha-follow"
    prof.mkdir(parents=True)
    a = st.Inbox.for_home(prof).post("Reply in thread?", "who asked what", "post it", key="slack:C1:1.0:x")
    b = st.Inbox().post("Root item", "b", "do")
    items = {i["session_id"]: i for i in c.get("/items").json()["items"]}
    assert items[a["session_id"]]["profile"] == "alpha-follow"
    assert items[b["session_id"]]["profile"] == "default"
    assert c.post(f"/items/{a['session_id']}/accept").status_code == 404  # wrong profile
    r = c.post(f"/items/{a['session_id']}/accept?profile=alpha-follow")
    assert r.status_code == 200 and r.json()["accept_prompt"] == "post it"
    assert a["session_id"] not in {i["session_id"] for i in c.get("/items").json()["items"]}
    assert c.post(f"/items/{b['session_id']}/archive").json() == {"ok": True}
    assert c.get("/items/x?profile=../etc").status_code == 400


def test_rest_post_passes_prompt_label(client):
    c, api, root = client
    r = c.post("/items", json={"title": "t", "body": "b", "accept_prompt": "the whole long prompt",
                               "prompt_label": "stop the idle box"})
    assert r.status_code == 200, r.text
    from hermes_state import SessionDB
    db = SessionDB(db_path=root / "state.db")
    try:
        content = db.get_messages(r.json()["session_id"])[0]["content"]
    finally:
        db.close()
    assert "**Do it:** stop the idle box" in content and "the whole long prompt" not in content
